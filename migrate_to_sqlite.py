import json
import os
from services.database import Database, init_db

JSON_PATH = os.path.join(os.path.dirname(__file__), 'data', 'trading.json')

def map_side(side):
    side = side.upper()
    if side in ('LONG', 'SHORT'):
        return side
    if side in ('BUY', 'LONG'):
        return 'LONG'
    if side in ('SELL', 'SHORT'):
        return 'SHORT'
    return 'LONG'

def migrate():
    if not os.path.exists(JSON_PATH):
        print('JSON-файл не найден. Миграция не требуется.')
        return

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    open_count = len(data.get('open_trades', []))
    closed_count = len(data.get('closed_trades', []))

    for trade in data.get('open_trades', []):
        Database.add_open_trade({
            'symbol': trade['symbol'],
            'side': map_side(trade.get('side', 'LONG')),
            'entry_price': float(trade.get('entryPrice', trade.get('entry_price', 0))),
            'quantity': abs(float(trade.get('positionAmt', trade.get('size', 0)))),
            'leverage': int(trade.get('leverage', 1)),
            'unrealized_pnl': float(trade.get('unrealizedPnl', trade.get('unrealized_pnl', 0)))
        })

    for trade in data.get('closed_trades', []):
        Database.add_closed_trade({
            'symbol': trade['symbol'],
            'side': map_side(trade.get('side', 'LONG')),
            'entry_price': float(trade.get('entryPrice', trade.get('entry_price', 0))),
            'exit_price': float(trade.get('exitPrice', trade.get('exit_price', trade.get('price', 0)))),
            'quantity': abs(float(trade.get('executedQty', trade.get('size', 0)))),
            'realized_pnl': float(trade.get('realizedPnl', trade.get('pnl', 0))),
            'comment': trade.get('comment', '')
        })

    os.rename(JSON_PATH, JSON_PATH + '.backup')
    print(f'Миграция завершена! Перенесено {open_count} открытых и {closed_count} закрытых сделок.')
    print(f'Старый файл сохранён как {JSON_PATH}.backup')

if __name__ == '__main__':
    init_db()
    migrate()