async def get_open_positions() -> dict:
    path = '/openApi/swap/v2/user/positions'
    result = await _request_with_retry('GET', path)

    if result.get('code') == 0:
        positions = result.get('data', [])
        if not isinstance(positions, list):
            positions = positions.get('positions', []) if isinstance(positions, dict) else []
        trades = []
        for pos in positions:
            amt = float(pos.get('positionAmt', 0))
            if amt != 0:
                # Composite key: symbol + side — надёжнее positionId при частичном закрытии
                symbol = pos.get('symbol', '')
                side = 'LONG' if amt > 0 else 'SHORT'
                position_id = pos.get('positionId') or f"{symbol}_{side}"
                trades.append({
                    'orderId':       position_id,
                    'symbol':        symbol,
                    'side':          side,
                    'entryPrice':    float(pos.get('avgPrice', 0)),
                    'positionAmt':   abs(amt),
                    'size':          abs(amt),
                    'unrealizedPnl': float(pos.get('unrealizedProfit', 0)),
                    'leverage':      pos.get('leverage', 1),
                    'stopLoss':      pos.get('stopLoss') or pos.get('stopLossPrice') or None,
                    'takeProfit':    pos.get('takeProfit') or pos.get('takeProfitPrice') or None,
                    'status':        'OPEN',
                })
        return {'success': True, 'trades': trades}
    else:
        return {
            'success': False,
            'error': result.get('msg', 'Неизвестная ошибка'),
            'trades': []
        }
