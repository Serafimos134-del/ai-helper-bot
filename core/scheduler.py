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
        if new_closed > 0:
            last_trades = db.get_closed_trades(limit=3)
            if len(last_trades) >= 3 and all(t['realized_pnl'] < 0 for t in last_trades):
                alert = (
                    "⚠️ *Обнаружена серия из 3 убыточных сделок!*\n"
                    "Рекомендую сделать паузу и проанализировать причины.\n"
                    "Используйте /ai_fix для AI-разбора."
                )
                await context.bot.send_message(chat_id=chat_id, text=alert, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка авто-синхронизации: {e}")


async def update_pinned_status(context: ContextTypes.DEFAULT_TYPE, db: Database, chat_id: str):
    if not chat_id:
        return
    try:
        balance = get_balance()
        open_positions = db.get_open_trades()

        text = "📌 *Текущий статус*\n\n"
        if balance.get('success'):
            text += (
                f"💰 Баланс: ${balance['equity']:.2f}\n"
                f"Доступно: ${balance['available']:.2f}\n"
                f"Маржа: ${balance['used_margin']:.2f}\n"
                f"Нереализ. PNL: ${balance['unrealized_pnl']:.2f}\n\n"
            )
        else:
            text += "❌ Не удалось получить баланс\n\n"

        if open_positions:
            text += "*Открытые позиции:*\n"
            for pos in open_positions:
                text += f"- {pos['symbol']} {pos['side']} (Pnl: {pos.get('unrealized_pnl', 0):.2f})\n"
        else:
            text += "🔓 Нет открытых позиций"

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
            except:
                pass

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='Markdown'
        )
        await msg.pin()
        context.bot_data['pinned_msg_id'] = msg.message_id

    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")