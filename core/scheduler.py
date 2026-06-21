import asyncio
import logging
import os
from telegram.ext import ContextTypes
from services.bingx_api import get_balance
from services.database import Database
from services.auto_sync import sync_trades

logger = logging.getLogger(__name__)


def setup_scheduler(app, db: Database, chat_id: str) -> None:
    """Регистрирует задачи автосинхронизации и обновления статуса."""
    app.job_queue.run_repeating(
        lambda c: auto_sync_job(c, db, chat_id),
        interval=15,
        first=10
    )
    app.job_queue.run_repeating(
        lambda c: update_pinned_status(c, db, chat_id),
        interval=300,
        first=30
    )


async def auto_sync_job(context: ContextTypes.DEFAULT_TYPE, db: Database, chat_id: str):
    if not chat_id:
        logger.warning("TELEGRAM_CHAT_ID не задан, авто-синхронизация пропущена")
        return
    try:
        results = await sync_trades(context.bot, chat_id)
        new_closed = len(results.get('new_closed', []))
        new_open = len(results.get('new_open', []))
        
        # Если есть изменения — обновить статус сразу
        if new_closed > 0 or new_open > 0:
            await update_pinned_status(context, db, chat_id, force=True)
        
        if new_closed > 0:
            last_trades = await asyncio.to_thread(db.get_closed_trades, limit=3)
            if len(last_trades) >= 3 and all(t['realized_pnl'] < 0 for t in last_trades):
                alert = (
                    "⚠️ *Обнаружена серия из 3 убыточных сделок!*\n"
                    "Рекомендую сделать паузу и проанализировать причины.\n"
                    "Используйте /ai_fix для AI-разбора."
                )
                await context.bot.send_message(chat_id=chat_id, text=alert, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка авто-синхронизации: {e}")


def _build_status_text(balance: dict, open_positions: list) -> str:
    """Собирает текст статус-сообщения с динамическим entry size."""
    text = "📌 *Текущий статус*\n\n"
    
    if balance.get('success'):
        equity = balance['equity']
        available = balance['available']
        used_margin = balance['used_margin']
        unrealized_pnl = balance['unrealized_pnl']
        
        # Динамический расчёт entry size
        entry_size = equity * 0.10
        add_size = equity * 0.03
        
        text += (
            f"💰 *Баланс*\n"
            f"Эквити: ${equity:,.2f}\n"
            f"Доступно: ${available:,.2f}\n"
            f"Маржа: ${used_margin:,.2f}\n"
            f"Нереализ. PNL: ${unrealized_pnl:+,.2f}\n\n"
            f"📐 *Вход:* ${entry_size:,.2f} (10% от депо)\n"
            f"📐 *Добор:* ${add_size:,.2f}–${equity * 0.05:,.2f}\n\n"
        )
    else:
        text += "❌ Не удалось получить баланс\n\n"
        equity = 0

    if open_positions:
        text += "*Открытые позиции:*\n"
        for pos in open_positions:
            symbol = pos.get('symbol', '?')
            side = pos.get('side', '?')
            pnl = pos.get('unrealized_pnl', 0)
            emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            text += f"{emoji} {symbol} {side} | PnL: ${pnl:+,.2f}\n"
        text += f"\n🔒 *Всего позиций:* {len(open_positions)}/2\n"
    else:
        text += "🔓 *Нет открытых позиций*\n\n"

    # Trading rules block (фиксированный)
    text += (
        "\n📋 *Правила:*\n"
        "• Вход: 10% от депо\n"
        "• Плечо: x5 BTC/ETH, x3 ALT\n"
        "• Маржа: Cross\n"
        "• Добор: 3–5% от депо, max 2\n"
        "• Опоздал = пропуск\n"
        "• Max 2 сделки одновременно\n"
        "• SL: −2% цены против позиции\n"
        "• TP: +20% PnL → фикс 50% + SL в Б/У\n"
        "• Daily stop: 2 стопа или −5% депо"
    )

    return text, equity


def _make_state_key(balance: dict, open_positions: list) -> str:
    """Создаёт ключ для сравнения состояний."""
    if not balance.get('success'):
        return "no_balance"
    parts = [
        f"eq={balance['equity']:.2f}",
        f"av={balance['available']:.2f}",
        f"um={balance['used_margin']:.2f}",
        f"up={balance['unrealized_pnl']:.2f}",
    ]
    for pos in open_positions:
        parts.append(f"{pos.get('symbol')}:{pos.get('side')}:{pos.get('unrealized_pnl', 0):.2f}")
    return "|".join(parts)


async def update_pinned_status(context: ContextTypes.DEFAULT_TYPE, db: Database, chat_id: str, force: bool = False):
    if not chat_id:
        return
    try:
        balance = await get_balance()
        open_positions = await asyncio.to_thread(db.get_open_trades)

        # Проверяем изменилось ли состояние
        state_key = _make_state_key(balance, open_positions)
        last_key = context.bot_data.get('last_state_key', '')
        
        if not force and state_key == last_key:
            return  # Ничего не изменилось — не обновляем
        
        context.bot_data['last_state_key'] = state_key

        # Собираем текст с динамическим entry size
        text, equity = _build_status_text(balance, open_positions)
        context.bot_data['last_equity'] = equity

        pinned_msg_id = context.bot_data.get('pinned_msg_id')
        if pinned_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=pinned_msg_id,
                    text=text,
                    parse_mode='Markdown'
                )
                return
            except Exception:
                logger.debug("Не удалось отредактировать pinned сообщение, создаю новое")

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='Markdown'
        )
        await msg.pin()
        context.bot_data['pinned_msg_id'] = msg.message_id

    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")