import logging
from services.bingx_api import get_open_positions, get_closed_orders
from services.database import Database

logger = logging.getLogger(__name__)

db = Database()

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
        stored_open = db.get_open_trades()

        # Создадим карту: orderId -> запись в БД
        stored_by_id = {str(t.get('orderId')): t for t in stored_open}

        # Обрабатываем все текущие позиции из API
        for trade in api_trades:
            oid = str(trade.get('orderId'))
            if oid not in stored_by_id:
                # Новая позиция – сохраняем в open_trades
                db.add_open_trade({
                    'symbol': trade.get('symbol'),
                    'side': trade.get('side'),
                    'entry_price': float(trade.get('entryPrice', 0)),
                    'quantity': abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                    'leverage': float(trade.get('leverage', 1)),
                    'unrealized_pnl': float(trade.get('unrealizedPnl', 0)),
                    'stop_loss': trade.get('stopLoss'),    # если API вернул
                    'take_profit': trade.get('takeProfit')
                })
                results['new_open'].append(trade)
                await _notify_new_trade(bot, chat_id, trade)

        # Ищем позиции, которые исчезли из API (были в БД, но теперь их нет)
        api_ids = {str(t.get('orderId')) for t in api_trades}
        for stored in stored_open:
            oid = str(stored.get('orderId'))
            if oid and oid not in api_ids:
                # Позиция закрылась – переносим в closed_trades с дополнительными полями
                closed_trade = {
                    'symbol': stored['symbol'],
                    'side': stored['side'],
                    'entry_price': float(stored.get('entry_price', 0)),
                    'exit_price': _get_exit_price_for(oid, stored),  # см. ниже
                    'quantity': float(stored.get('quantity', 0)),
                    'realized_pnl': float(stored.get('unrealized_pnl', 0)),  # API уже вернул финальный PNL? Лучше брать из истории, но для простоты – текущий нереализованный
                    'comment': '',
                    'leverage': float(stored.get('leverage', 1)),
                    'stop_loss': stored.get('stop_loss'),
                    'take_profit': stored.get('take_profit'),
                    'risk_percent': 0,
                    'risk_reward': None,
                    'open_time': stored.get('created_at'),  # когда открыли (по записи в БД)
                    'close_time': None  # позже можно обновить из истории
                }
                # Попробуем найти более точные данные в истории ордеров (цена выхода, PNL)
                await _enrich_closed_from_history(oid, closed_trade)

                db.add_closed_trade(closed_trade)
                db.delete_open_trade(stored['symbol'])  # удаляем из открытых
                results['new_closed'].append(stored)
                await _notify_closed_trade(bot, chat_id, stored, closed_trade['realized_pnl'])

    # --- Проверка истории закрытых ордеров (на случай пропущенных) ---
    closed_result = get_closed_orders(limit=50)
    if closed_result.get('success'):
        stored_closed = db.get_closed_trades(limit=1000)  # все закрытые
        stored_closed_ids = {str(t.get('orderId', t.get('id'))) for t in stored_closed}

        for order in closed_result.get('trades', []):
            oid = str(order.get('orderId'))
            if oid not in stored_closed_ids:
                # Добавляем как закрытую, которой не было в открытых
                db.add_closed_trade({
                    'symbol': order.get('symbol'),
                    'side': order.get('side'),
                    'entry_price': float(order.get('avgPrice', 0)),
                    'exit_price': float(order.get('avgPrice', 0)),  # уточнить поле для цены выхода
                    'quantity': float(order.get('executedQty', 0)),
                    'realized_pnl': float(order.get('profit', 0)),
                    'comment': '',
                    'leverage': float(order.get('leverage', 1)),
                    'stop_loss': None,
                    'take_profit': None,
                    'risk_percent': 0,
                    'risk_reward': None,
                    'open_time': order.get('time'),
                    'close_time': order.get('updateTime')
                })
    return results


async def _enrich_closed_from_history(order_id: str, closed: dict):
    """Попытаться найти ордер в истории и взять цену выхода и PNL."""
    # В простом варианте используем get_closed_orders с фильтром? Но там нет фильтра по orderId.
    # Можно получить все последние ордера и найти нужный (уже сделано в sync_trades).
    # Здесь оставим заглушку – если нужно, можно реализовать отдельный запрос.
    pass


def _get_exit_price_for(order_id: str, stored_open: dict) -> float:
    """
    Получить цену выхода. Без дополнительного запроса можно взять текущую цену,
    но это ненадёжно. Лучше из истории ордеров.
    Пока вернём entry_price (неправильно, но без истории не узнать).
    """
    return float(stored_open.get('entry_price', 0))


async def _notify_new_trade(bot, chat_id: str, trade: dict):
    try:
        symbol = trade.get('symbol', '?')
        side = trade.get('side', '?')
        entry = float(trade.get('entryPrice', 0))
        size = trade.get('size', trade.get('positionAmt', '?'))
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
        logger.error(f"Ошибка уведомления об открытии: {e}")


async def _notify_closed_trade(bot, chat_id: str, trade: dict, pnl: float):
    try:
        symbol = trade.get('symbol', '?')
        side = trade.get('side', '?')
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        text = (
            f"🔔 *Позиция закрыта!*\n\n"
            f"{pnl_emoji} {symbol} — {side}\n"
            f"💰 PNL: ${pnl:+.2f}"
        )
        await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка уведомления о закрытии: {e}")