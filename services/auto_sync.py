import asyncio
import logging
from datetime import datetime, timezone
from services.bingx_api import get_open_positions, get_closed_orders, get_ticker
from services.database import Database
from services.ai_trading import AITradingAnalyzer
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

db = Database()
ai_analyzer = AITradingAnalyzer()

async def sync_trades(bot, chat_id: str) -> dict:
    results = {'new_open': [], 'new_closed': []}

    # Очистка устаревших записей без orderId
    db.cleanup_orphan_open_trades()

    # --- Открытые позиции ---
    open_result = get_open_positions()
    if not open_result.get('success'):
        logger.warning(f"Ошибка получения открытых позиций: {open_result.get('error')}")
    else:
        api_trades = open_result.get('trades', [])
        stored_open = db.get_open_trades()
        stored_by_id = {}
        for t in stored_open:
            oid = str(t.get('orderId')) if t.get('orderId') else None
            if oid:
                stored_by_id[oid] = t

        for trade in api_trades:
            oid = str(trade.get('orderId'))
            raw_side = trade.get('side', '')
            side = 'LONG' if raw_side in ('BUY', 'LONG') else 'SHORT'

            if oid in stored_by_id:
                db.update_open_trade_by_order_id(
                    oid,
                    unrealized_pnl=float(trade.get('unrealizedPnl', 0)),
                    leverage=float(trade.get('leverage', 1)),
                    quantity=abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                    entry_price=float(trade.get('entryPrice', 0)),
                    stop_loss=trade.get('stopLoss'),
                    take_profit=trade.get('takeProfit')
                )
                stored_by_id.pop(oid)
            else:
                db.add_open_trade({
                    'orderId': trade.get('orderId'),
                    'symbol': trade.get('symbol'),
                    'side': side,
                    'entry_price': float(trade.get('entryPrice', 0)),
                    'quantity': abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                    'leverage': float(trade.get('leverage', 1)),
                    'unrealized_pnl': float(trade.get('unrealizedPnl', 0)),
                    'stop_loss': trade.get('stopLoss'),
                    'take_profit': trade.get('takeProfit'),
                    'entry_comment': ''
                })
                results['new_open'].append(trade)
                await _notify_new_trade(bot, chat_id, trade)

        # Закрытые позиции
        for oid, stored in stored_by_id.items():
            closed_trade = _build_closed_trade(stored)
            db.add_closed_trade(closed_trade)
            db.delete_open_trade_by_order_id(oid)
            last_id = db.get_last_closed_id()
            results['new_closed'].append(stored)
            await _notify_closed_trade(bot, chat_id, stored, closed_trade['realized_pnl'], last_id)
            asyncio.ensure_future(_auto_ai_review(last_id, closed_trade))

    # --- История закрытых ордеров (все, включая нулевые) ---
    closed_result = get_closed_orders(limit=50)
    if closed_result.get('success'):
        stored_closed = db.get_closed_trades(limit=1000)
        stored_closed_ids = {str(t.get('orderId', t.get('id'))) for t in stored_closed}
        for order in closed_result.get('trades', []):
            oid = str(order.get('orderId'))
            if oid not in stored_closed_ids:
                raw_side = order.get('side', 'BUY')
                side = 'LONG' if raw_side in ('BUY', 'LONG') else 'SHORT'
                open_time = order.get('time')
                close_time = order.get('updateTime')
                holding_minutes = None
                if open_time and close_time:
                    try:
                        diff = (close_time - open_time) / 1000 / 60
                        holding_minutes = int(diff)
                    except Exception:
                        pass
                btc_price, eth_price, market_trend = _get_market_data()
                db.add_closed_trade({
                    'symbol': order.get('symbol'),
                    'side': side,
                    'entry_price': float(order.get('avgPrice', 0)),
                    'exit_price': float(order.get('avgPrice', 0)),
                    'quantity': float(order.get('executedQty', 0)),
                    'realized_pnl': float(order.get('profit', 0)),
                    'comment': '',
                    'leverage': float(order.get('leverage', 1)),
                    'stop_loss': None,
                    'take_profit': None,
                    'risk_percent': 0,
                    'risk_reward': None,
                    'open_time': open_time,
                    'close_time': close_time,
                    'entry_comment': '',
                    'exit_comment': '',
                    'ai_review': '',
                    'holding_minutes': holding_minutes,
                    'btc_price': btc_price,
                    'eth_price': eth_price,
                    'market_trend': market_trend,
                    'setup_type': None,
                    'mistakes': None,
                    'ai_score': None
                })
    return results


# Вспомогательные функции (_build_closed_trade, _get_market_data и т.д.) – те же, что и раньше
# Они уже корректны, вставлять их сюда не нужно, если файл полный.
# В репозитории должен лежать полный файл, включая эти функции.