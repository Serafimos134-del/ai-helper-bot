import json
import os
from services.database import Database, init_db

JSON_PATH = os.path.join(os.path.dirname(__file__), 'data', 'trading.json')

def map_side(side):
    side = side.upper()
    if side in ('LONG', 'SHORT'):
        return side
    if side == 'BUY':
        return 'LONG'
    if side == 'SELL':
        return 'SHORT'
    return 'LONG'

def migrate():
    if not os.path.exists(JSON_PATH):
        print('JSON-файл не найден. Миграция не требуется.')
        return

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    open_trades = data.get('open_trades', [])
    closed_trades = data.get('closed_trades', [])

    db = Database()
    migrated_open = 0
    migrated_closed = 0

    def _order_id(trade: dict, prefix: str, idx: int) -> str:
        oid = trade.get('orderId') or trade.get('orderid') or trade.get('id')
        return str(oid) if oid else f'migrated-{prefix}-{idx}'

    for idx, trade in enumerate(open_trades):
        try:
            db.add_open_trade({
                'symbol': trade['symbol'],
                'side': map_side(trade.get('side', 'LONG')),
                'entry_price': float(trade.get('entryPrice', trade.get('entry_price', 0))),
                'quantity': abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                'leverage': int(trade.get('leverage', 1)),
                'unrealized_pnl': float(trade.get('unrealizedPnl', trade.get('unrealized_pnl', 0))),
                # orderId обязателен в схеме БД (add_open_trade падает без него) —
                # если в старых данных его нет, генерируем синтетический.
                'orderId': _order_id(trade, 'open', idx)
            })
            migrated_open += 1
        except Exception as e:
            print(f'Пропущена открытая сделка #{idx} ({trade.get("symbol", "?")}): {e}')

    for idx, trade in enumerate(closed_trades):
        try:
            db.add_closed_trade({
                'symbol': trade['symbol'],
                'side': map_side(trade.get('side', 'LONG')),
                'entry_price': float(trade.get('entryPrice', trade.get('entry_price', 0))),
                'exit_price': float(trade.get('exitPrice', trade.get('exit_price', trade.get('price', 0)))),
                'quantity': abs(float(trade.get('executedQty', trade.get('size', 0)))),
                'realized_pnl': float(trade.get('realizedPnl', trade.get('pnl', 0))),
                'comment': trade.get('comment', ''),
                'orderId': _order_id(trade, 'closed', idx)
            })
            migrated_closed += 1
        except Exception as e:
            print(f'Пропущена закрытая сделка #{idx} ({trade.get("symbol", "?")}): {e}')

    os.rename(JSON_PATH, JSON_PATH + '.backup')
    print(f'Миграция завершена! Перенесено {migrated_open}/{len(open_trades)} открытых '
          f'и {migrated_closed}/{len(closed_trades)} закрытых сделок.')
    print(f'Старый файл сохранён как {JSON_PATH}.backup')

if __name__ == '__main__':
    init_db()
    migrate()