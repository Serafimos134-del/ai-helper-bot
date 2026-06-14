import logging
from services.bingx_api import get_open_positions, get_closed_orders
from services.trading_storage import (
    get_open_trades,
    add_trade,
    close_trade,
    get_closed_trades
)

logger = logging.getLogger(__name__)


async def sync_trades(bot, chat_id: str) -> dict:
    """
    Синхронизировать сделки с BingX.
    Проверяет новые открытые позиции и закрытые ордера.
    Возвращает словарь с результатами: new_open, new_closed.
    """
    results = {'new_open': [], 'new_closed': []}

    # --- Проверка открытых позиций ---
    open_result = get_open_positions()
    if not open_result.get('success'):
        logger.warning(f"Ошибка получения открытых позиций: {open_result.get('error')}")
    else:
        api_trades = open_result.get('trades', [])
        stored_open = get_open_trades()
        stored_ids = {str(t.get('orderId')) for t in stored_open}
        api_ids = {str(t.get('orderId')) for t in api_trades}

        # Новые позиции (открылись)
        for trade in api_trades:
            if str(trade.get('orderId')) not in stored_ids:
                added = add_trade(trade)
                if added:
                    results['new_open'].append(trade)
                    await _notify_new_trade(bot, chat_id, trade)

        # Закрытые позиции (были в хранилище, но исчезли из API)
        for trade in stored_open:
            if str(trade.get('orderId')) not in api_ids:
                close_trade(str(trade.get('orderId')))
                results['new_closed'].append(trade)
                await _notify_closed_trade(bot, chat_id, trade)

    # --- Проверка истории закрытых ордеров ---
    closed_result = get_closed_orders(limit=50)
    if closed_result.get('success'):
        stored_closed = get_closed_trades()
        stored_closed_ids = {str(t.get('orderId')) for t in stored_closed}

        for order in closed_result.get('trades', []):
            oid = str(order.get('orderId'))
            if oid not in stored_closed_ids:
                # Добавляем как закрытую, которую ранее не отслеживали
                order['status'] = 'CLOSED'
                add_trade(order)
                close_trade(oid, {'realizedPnl': order.get('realizedPnl', 0)})

    return results


async def _notify_new_trade(bot, chat_id: str, trade: dict):
    """Отправить уведомление об открытии новой позиции."""
    try:
        symbol = trade.get('symbol', '?')
        side = trade.get('side', '?')
        entry = float(trade.get('entryPrice', 0))
        size = float(trade.get('size', 0))
        leverage = trade.get('leverage', 1)

        side_emoji = "🟢" if side == 'LONG' else "🔴"
        text = (
            f"🔔 *Новая позиция открыта!*\n\n"
            f"{side_emoji} {symbol} — {side}\n"
            f"💵 Цена входа: ${entry:.4f}\n"
            f"📦 Размер: {size}\n"
            f"⚡️ Плечо: {leverage}x"
        )
        await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об открытии: {e}")


async def _notify_closed_trade(bot, chat_id: str, trade: dict):
    """Отправить уведомление о закрытии позиции."""
    try:
        symbol = trade.get('symbol', '?')
        side = trade.get('side', '?')
        pnl = float(trade.get('unrealizedPnl', trade.get('realizedPnl', 0)))

        pnl_emoji = "✅" if pnl >= 0 else "❌"
        text = (
            f"🔔 *Позиция закрыта!*\n\n"
            f"{pnl_emoji} {symbol} — {side}\n"
            f"💰 PNL: ${pnl:+.2f}"
        )
        await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о закрытии: {e}")
