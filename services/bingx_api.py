import hashlib
import hmac
import time
import requests
import os
from urllib.parse import urlencode

BINGX_API_KEY = os.getenv('BINGX_API_KEY', '')
BINGX_SECRET_KEY = os.getenv('BINGX_SECRET_KEY', '')
BASE_URL = 'https://open-api.bingx.com'


def _get_timestamp() -> str:
    return str(int(time.time() * 1000))


def _sign(params: dict) -> str:
    """Создать подпись HMAC-SHA256 для запроса."""
    query_string = urlencode(sorted(params.items()))
    signature = hmac.new(
        BINGX_SECRET_KEY.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


def _request(method: str, path: str, params: dict = None) -> dict:
    """Выполнить подписанный запрос к BingX API."""
    if params is None:
        params = {}

    params['timestamp'] = _get_timestamp()
    params['signature'] = _sign(params)

    headers = {
        'X-BX-APIKEY': BINGX_API_KEY,
        'Content-Type': 'application/json'
    }

    url = BASE_URL + path
    try:
        if method == 'GET':
            response = requests.get(url, params=params, headers=headers, timeout=10)
        else:
            response = requests.post(url, json=params, headers=headers, timeout=10)

        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {'error': 'Unexpected response format', 'code': -1, 'raw': data}
        return data
    except requests.exceptions.RequestException as e:
        return {'error': str(e), 'code': -1}
    except ValueError as e:
        return {'error': f'Invalid JSON response: {e}', 'code': -1}


def get_balance() -> dict:
    """Получить баланс аккаунта (Perpetual Futures)."""
    path = '/openApi/swap/v2/user/balance'
    result = _request('GET', path)

    if result.get('code') == 0:
        data = result.get('data', {})
        balance = data.get('balance', {})
        return {
            'success': True,
            'equity': float(balance.get('equity', 0)),
            'available': float(balance.get('availableMargin', 0)),
            'used_margin': float(balance.get('usedMargin', 0)),
            'unrealized_pnl': float(balance.get('unrealizedProfit', 0)),
            'currency': 'USDT'
        }
    else:
        return {
            'success': False,
            'error': result.get('msg', 'Неизвестная ошибка'),
            'code': result.get('code', -1)
        }


def get_open_positions() -> dict:
    """Получить открытые позиции."""
    path = '/openApi/swap/v2/user/positions'
    result = _request('GET', path)

    if result.get('code') == 0:
        positions = result.get('data', [])
        if not isinstance(positions, list):
            positions = positions.get('positions', []) if isinstance(positions, dict) else []
        trades = []
        for pos in positions:
            if float(pos.get('positionAmt', 0)) != 0:
                trades.append({
                    'orderId': pos.get('positionId', pos.get('symbol')),
                    'symbol': pos.get('symbol', ''),
                    'side': 'LONG' if float(pos.get('positionAmt', 0)) > 0 else 'SHORT',
                    'entryPrice': float(pos.get('avgPrice', 0)),
                    'size': abs(float(pos.get('positionAmt', 0))),
                    'unrealizedPnl': float(pos.get('unrealizedProfit', 0)),
                    'leverage': pos.get('leverage', 1),
                    'status': 'OPEN'
                })
        return {'success': True, 'trades': trades}
    else:
        return {
            'success': False,
            'error': result.get('msg', 'Неизвестная ошибка'),
            'trades': []
        }


def get_closed_orders(symbol: str = '', limit: int = 20) -> dict:
    """Получить историю закрытых ордеров."""
    path = '/openApi/swap/v2/trade/allOrders'
    params = {'limit': limit}
    if symbol:
        params['symbol'] = symbol

    result = _request('GET', path, params)

    if result.get('code') == 0:
        orders = result.get('data', {}).get('orders', [])
        closed = []
        for order in orders:
            if order.get('status') in ('FILLED', 'CANCELED'):
                closed.append({
                    'orderId': order.get('orderId', ''),
                    'symbol': order.get('symbol', ''),
                    'side': order.get('side', ''),
                    'price': float(order.get('avgPrice', 0)),
                    'size': float(order.get('executedQty', 0)),
                    'realizedPnl': float(order.get('profit', 0)),
                    'status': order.get('status', ''),
                    'time': order.get('time', ''),
                    'updateTime': order.get('updateTime', '')
                })
        return {'success': True, 'trades': closed}
    else:
        return {
            'success': False,
            'error': result.get('msg', 'Неизвестная ошибка'),
            'trades': []
        }


def get_top_tickers(limit: int = 10) -> dict:
    """Публичные данные по топ парам (без подписи)."""
    url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tickers = data.get('data', [])
        if not isinstance(tickers, list):
            return {'success': False, 'error': 'Unexpected format', 'tickers': []}

        # Сортируем по объёму за 24ч (убывание)
        sorted_tickers = sorted(
            tickers,
            key=lambda x: float(x.get('quoteVolume', 0)),
            reverse=True
        )
        return {'success': True, 'tickers': sorted_tickers[:limit]}
    except Exception as e:
        return {'success': False, 'error': str(e), 'tickers': []}


def get_kline(symbol: str = "BTC-USDT", interval: str = "1h", limit: int = 24) -> dict:
    """Публичные свечные данные (без подписи)."""
    url = f"{BASE_URL}/openApi/swap/v3/quote/klines"
    params = {
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        klines = data.get('data', [])
        if not isinstance(klines, list):
            return {'success': False, 'error': 'Unexpected format', 'klines': []}
        return {'success': True, 'klines': klines}
    except Exception as e:
        return {'success': False, 'error': str(e), 'klines': []}


def get_ticker(symbol: str) -> dict:
    """Публичные данные по одному символу (без подписи)."""
    url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
    params = {'symbol': symbol}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tickers = data.get('data', [])
        if not isinstance(tickers, list) or not tickers:
            return {'success': False, 'error': 'Symbol not found', 'ticker': {}}
        return {'success': True, 'ticker': tickers[0]}
    except Exception as e:
        return {'success': False, 'error': str(e), 'ticker': {}}