import hashlib
import hmac
import time
import asyncio
import os
from urllib.parse import urlencode
import httpx

BINGX_API_KEY = os.getenv('BINGX_API_KEY', '')
BINGX_SECRET_KEY = os.getenv('BINGX_SECRET_KEY', '')
BASE_URL = 'https://open-api.bingx.com'

MAX_RETRIES = 2
RETRY_DELAY = 1


def _get_timestamp() -> str:
    return str(int(time.time() * 1000))


def _sign(params: dict) -> str:
    query_string = urlencode(sorted(params.items()))
    signature = hmac.new(
        BINGX_SECRET_KEY.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


async def _request_with_retry(method: str, path: str, params: dict = None) -> dict:
    for attempt in range(MAX_RETRIES + 1):
        result = await _request(method, path, params)
        if result.get('code') != -1:
            return result
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    return result


async def _request(method: str, path: str, params: dict = None) -> dict:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
            if method == 'GET':
                response = await client.get(url, params=params, headers=headers)
            else:
                response = await client.post(url, json=params, headers=headers)

            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return {'error': 'Unexpected response format', 'code': -1, 'raw': data}
            return data
    except httpx.HTTPError as e:
        return {'error': str(e), 'code': -1}
    except ValueError as e:
        return {'error': f'Invalid JSON response: {e}', 'code': -1}


async def _public_request_with_retry(url: str, params: dict = None) -> dict:
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
            else:
                return {'error': str(e), 'code': -1}


async def get_balance() -> dict:
    path = '/openApi/swap/v2/user/balance'
    result = await _request_with_retry('GET', path)

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


async def get_open_positions() -> dict:
    path = '/openApi/swap/v2/user/positions'
    result = await _request_with_retry('GET', path)

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


async def get_closed_orders(symbol: str = '', limit: int = 20) -> dict:
    path = '/openApi/swap/v2/trade/allOrders'
    params = {'limit': limit}
    if symbol:
        params['symbol'] = symbol

    result = await _request_with_retry('GET', path, params)

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


async def get_top_tickers(limit: int = 10) -> dict:
    url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
    data = await _public_request_with_retry(url)

    if isinstance(data, dict) and data.get('code') == 0:
        tickers = data.get('data', [])
        if not isinstance(tickers, list):
            return {'success': False, 'error': 'Unexpected format', 'tickers': []}
        sorted_tickers = sorted(
            tickers,
            key=lambda x: float(x.get('quoteVolume', 0)),
            reverse=True
        )
        return {'success': True, 'tickers': sorted_tickers[:limit]}
    else:
        return {'success': False, 'error': data.get('error', 'Unknown error'), 'tickers': []}


async def get_kline(symbol: str = "BTC-USDT", interval: str = "1h", limit: int = 24) -> dict:
    url = f"{BASE_URL}/openApi/swap/v3/quote/klines"
    params = {
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    }
    data = await _public_request_with_retry(url, params)

    if isinstance(data, dict) and data.get('code') == 0:
        klines = data.get('data', [])
        if not isinstance(klines, list):
            return {'success': False, 'error': 'Unexpected format', 'klines': []}
        return {'success': True, 'klines': klines}
    else:
        return {'success': False, 'error': data.get('error', 'Unknown error'), 'klines': []}


async def get_ticker(symbol: str) -> dict:
    url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
    params = {'symbol': symbol}
    data = await _public_request_with_retry(url, params)

    if isinstance(data, dict) and data.get('code') == 0:
        ticker_data = data.get('data', {})
        if isinstance(ticker_data, list):
            if not ticker_data:
                return {'success': False, 'error': 'Symbol not found', 'ticker': {}}
            ticker_obj = ticker_data[0]
        elif isinstance(ticker_data, dict):
            ticker_obj = ticker_data
        else:
            return {'success': False, 'error': 'Unexpected format', 'ticker': {}}
        return {'success': True, 'ticker': ticker_obj}
    else:
        return {'success': False, 'error': data.get('error', 'Unknown error'), 'ticker': {}}


async def get_funding_rate(symbol: str) -> dict:
    url = f"{BASE_URL}/openApi/swap/v2/quote/premiumIndex"
    params = {'symbol': symbol}
    data = await _public_request_with_retry(url, params)

    if isinstance(data, dict) and data.get('code') == 0:
        result = data.get('data', {})
        if isinstance(result, list) and result:
            result = result[0]
        return {
            'success': True,
            'funding_rate': float(result.get('lastFundingRate', 0)),
            'mark_price': float(result.get('markPrice', 0)),
            'index_price': float(result.get('indexPrice', 0)),
        }
    else:
        return {'success': False, 'error': data.get('error', 'Unknown error')}


async def get_open_interest(symbol: str) -> dict:
    url = f"{BASE_URL}/openApi/swap/v2/quote/openInterest"
    params = {'symbol': symbol}
    data = await _public_request_with_retry(url, params)

    if isinstance(data, dict) and data.get('code') == 0:
        result = data.get('data', {})
        return {
            'success': True,
            'open_interest': float(result.get('openInterest', 0)),
        }
    else:
        return {'success': False, 'error': data.get('error', 'Unknown error')}


def _calculate_atr(klines: list, period: int = 14) -> float:
    if len(klines) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(klines)):
        prev = klines[i-1]
        curr = klines[i]
        high_prev = float(prev.get('high', 0))
        low_prev = float(prev.get('low', 0))
        close_prev = float(prev.get('close', 0))
        high_cur = float(curr.get('high', 0))
        low_cur = float(curr.get('low', 0))
        close_cur = float(curr.get('close', 0))

        tr = max(high_cur - low_cur, abs(high_cur - close_prev), abs(low_cur - close_prev))
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    atr = sum(true_ranges[-period:]) / period
    return atr


def _detect_market_regime(klines: list) -> str:
    if len(klines) < 20:
        return "UNKNOWN"

    closes = [float(k.get('close', 0)) for k in klines[-20:]]
    sma20 = sum(closes) / 20
    current_price = closes[-1]

    if current_price > sma20 * 1.02 and closes[-1] > closes[-3]:
        return "TRENDING_UP"
    elif current_price < sma20 * 0.98 and closes[-1] < closes[-3]:
        return "TRENDING_DOWN"
    else:
        return "RANGING"