"""
core/scheduler.py
Refactored scheduler with persistent pinned message state,
thread-safe status updates, restart resilience,
and Trade Management Engine v2 display.
"""

import asyncio
import logging
from telegram.ext import ContextTypes
from services.bingx_api import get_balance
from services.database import Database
from services.auto_sync import sync_trades
from services.trade_manager import TradeManager

logger = logging.getLogger(__name__)

_pinned_lock = asyncio.Lock()

MEMORY_CATEGORY = "bot_state"
KEY_PINNED_MSG_ID = "pinned_msg_id"
KEY_LAST_STATE = "last_state_key"
KEY_LAST_EQUITY = "last_equity"


def setup_scheduler(app, db: Database, chat_id: str) -> None:
    """Register auto-sync and status update jobs."""
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


def _build_status_text(balance: dict, open_positions: list, db: Database) -> str:
    """Build status message text with dynamic entry size and Trade Manager v2 display."""
    text = "📌 *Текущий статус*\n\n"

    equity = 0.0
    if balance.get('success'):
        equity = balance.get('equity') or 0.0
        available = balance.get('available') or 0.0
        used_margin = balance.get('used_margin') or 0.0
        unrealized_pnl = balance.get('unrealized_pnl') or 0.0

        entry_size = equity * 0.10 if equity > 0 else 0.0
        add_size = equity * 0.03 if equity > 0 else 0.0
        add_max = equity * 0.05 if equity > 0 else 0.0

        text += (
            f"💰 *Баланс*\n"
            f"Эквити: ${equity:,.2f}\n"
            f"Доступно: ${available:,.2f}\n"
            f"Маржа: ${used_margin:,.2f}\n"
            f"Нереализ. PNL: ${unrealized_pnl:+,.2f}\n\n"
            f"📐 *Вход:* ${entry_size:,.2f} (10% от депо)\n"
            f"📐 *Добор:* ${add_size:,.2f}–${add_max:,.2f}\n\n"
        )
    else:
        text += "❌ Не удалось получить баланс\n\n"

    if open_positions:
        tm = TradeManager(db)
        text += "*Открытые позиции:*\n"
        for pos in open_positions:
            symbol = pos.get('symbol', '?')
            side = pos.get('side', '?')
            pnl = pos.get('unrealized_pnl', 0) or 0.0
            emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            text += f"{emoji} {symbol} {side} | PnL: ${pnl:+,.2f}\n"

            # ─── Trade Manager v2 fields ───
            idea = pos.get('idea')
            if idea:
                text += f"   🎯 Идея: {idea}\n"
            dca_count = int(pos.get('dca_count', 0))
            max_dca = 2
            text += f"   📐 DCA: {dca_count}/{max_dca}\n"
            inval = pos.get('invalidation_sl')
            if inval:
                text += f"   🛑 Invalidation SL: ${float(inval):.4f}\n"
            tp_zones = tm.get_tp_zones(pos.get('orderId', ''))
            if tp_zones:
                zones_str = ', '.join([f"${z:.4f}" for z in tp_zones])
                text += f"   🎯 TP Zones: {zones_str}\n"

        text += f"\n🔒 *Всего позиций:* {len(open_positions)}/2\n"
    else:
        text += "🔓 *Нет открытых позиций*\n\n"

    text += (
        "\n📋 *Правила:*\n"
        "• Вход: 10% от депо\n"
        "• Плечо: x5 BTC/ETH, x3 ALT\n"
        "• Маржа: Cross\n"
        "• Добор: 3–5% от депо, max 2\n"
        "• Опоздал = пропуск\n"
        "• Max 2 сделки одновременно\n"
        "• SL: по инвалидации идеи\n"
        "• TP: по рыночным зонам (TP1, TP2, Runner)\n"
        "• Daily stop: 2 стопа или −5% депо"
    )

    return text, equity


def _make_state_key(balance: dict, open_positions: list) -> str:
    if not balance.get('success'):
        return "no_balance"
    parts = [
        f"eq={balance.get('equity', 0):.2f}",
        f"av={balance.get('available', 0):.2f}",
        f"um={balance.get('used_margin', 0):.2f}",
        f"up={balance.get('unrealized_pnl', 0):.2f}",
    ]
    for pos in open_positions:
        parts.append(
            f"{pos.get('symbol')}:{pos.get('side')}:{pos.get('unrealized_pnl', 0):.2f}"
            f":idea={pos.get('idea')}"
            f":sl={pos.get('invalidation_sl')}"
            f":dca={pos.get('dca_count')}"
            f":tp={pos.get('tp_zones')}"
        )
    return "|".join(parts)


def _get_pinned_msg_id(db: Database) -> int | None:
    raw = db.memory_get(MEMORY_CATEGORY, KEY_PINNED_MSG_ID)
    if raw:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None
    return None


def _save_pinned_msg_id(db: Database, msg_id: int):
    db.memory_set(MEMORY_CATEGORY, KEY_PINNED_MSG_ID, str(msg_id))


def _save_state(db: Database, state_key: str, equity: float):
    db.memory_set(MEMORY_CATEGORY, KEY_LAST_STATE, state_key)
    db.memory_set(MEMORY_CATEGORY, KEY_LAST_EQUITY, str(equity))


def _get_saved_state(db: Database) -> tuple:
    state_key = db.memory_get(MEMORY_CATEGORY, KEY_LAST_STATE) or ""
    equity_raw = db.memory_get(MEMORY_CATEGORY, KEY_LAST_EQUITY) or "0.0"
    try:
        equity = float(equity_raw)
    except (ValueError, TypeError):
        equity = 0.0
    return state_key, equity


async def update_pinned_status(context: ContextTypes.DEFAULT_TYPE, db: Database, chat_id: str, force: bool = False):
    if not chat_id:
        return

    async with _pinned_lock:
        try:
            balance = await get_balance()
            open_positions = await asyncio.to_thread(db.get_open_trades)

            state_key = _make_state_key(balance, open_positions)
            last_state_key, _ = _get_saved_state(db)

            if not force and state_key == last_state_key:
                return

            text, equity = _build_status_text(balance, open_positions, db)
            _save_state(db, state_key, equity)

            pinned_msg_id = _get_pinned_msg_id(db)
            if pinned_msg_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=pinned_msg_id,
                        text=text,
                        parse_mode='Markdown'
                    )
                    logger.debug(f"Pinned message {pinned_msg_id} updated")
                    return
                except Exception as e:
                    logger.warning(f"Failed to edit pinned message {pinned_msg_id}: {e}")
                    _save_pinned_msg_id(db, 0)

            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='Markdown'
            )
            await msg.pin()
            _save_pinned_msg_id(db, msg.message_id)
            logger.info(f"New pinned message created: {msg.message_id}")

        except Exception as e:
            logger.error(f"Error updating pinned status: {e}")