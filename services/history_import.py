"""
services/history_import.py
Импорт истории закрытых позиций с биржи (BingX positionHistory) —
единоразовое пополнение closed_trades для пользователей, у которых уже
есть торговая история ДО подключения ключей к боту. Без этого /riskscore
(ai/risk_profile.py, MIN_TRADES_FOR_SCORE=5) ждал бы, пока накопится 5
новых сделок через обычный периодический sync (services/auto_sync.py) —
что может занять недели для не самого активного трейдера.

orderId для импортированных строк — "hist_{positionId}" (не реальный
BingX orderId: positionHistory отдаёт positionId, другое пространство
идентификаторов, чем у обычных ордеров/allOrders) — префикс исключает
теоретическую коллизию с обычным потоком синхронизации и делает
импортированные строки узнаваемыми при отладке.
"""

import logging

from services.exchange_api import get_recent_closed_positions

logger = logging.getLogger(__name__)


async def import_trade_history(db, user_id: str, limit: int = 20) -> dict:
    result = await get_recent_closed_positions(limit=limit)
    if not result.get('success'):
        return {
            'success': False, 'error': result.get('error', 'неизвестная ошибка'),
            'imported': 0, 'skipped': 0, 'total_found': 0,
        }

    imported = 0
    skipped = 0
    for pos in result['positions']:
        position_id = pos.get('positionId')
        if not position_id:
            continue
        order_id = f"hist_{position_id}"
        existing = db._execute("SELECT id FROM closed_trades WHERE orderId = ?", (order_id,)).fetchone()
        if existing:
            skipped += 1
            continue
        trade = {
            'orderId': order_id,
            'symbol': pos.get('symbol'),
            'side': pos.get('side'),
            'entry_price': pos.get('entry_price', 0),
            'exit_price': pos.get('exit_price', 0),
            'quantity': pos.get('quantity', 0),
            'realized_pnl': pos.get('realized_pnl', 0),
            'leverage': pos.get('leverage', 1),
            'open_time': pos.get('open_time'),
            'close_time': pos.get('close_time'),
            'dca_count': 0,
            'user_id': user_id,
        }
        try:
            db.add_closed_trade(trade)
            imported += 1
        except Exception as e:
            logger.error(f"import_trade_history: не удалось вставить {order_id} для {user_id}: {e}")

    return {
        'success': True, 'imported': imported, 'skipped': skipped,
        'total_found': len(result['positions']),
    }
