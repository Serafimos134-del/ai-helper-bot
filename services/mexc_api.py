"""
services/mexc_api.py
MEXC Futures (Contract, USDT-M) — реализация под Exchange Adapter Layer
(services/exchanges/mexc.py), задача от 13.07.2026.

Структура и мультитенантный паттерн — см. services/bybit_api.py и docstring
там же (то же самое НЕ протестировано на реальном аккаунте, ключи будут
переданы после реализации).

Символы MEXC — с подчёркиванием ("BTC_USDT"), нормализуются в "BTC-USDT"
на выходе — та же причина, что и у bybit_api.py/binance_api.py.

В отличие от BingX/Bybit/Binance, MEXC отдаёт РЕАЛЬНЫЙ стабильный
positionId и на открытых, и на закрытых позициях — здесь не нужен
псевдо-ID вида "SYMBOL_SIDE" (см. AUDIT.md/services/database.py — именно
такой псевдо-ID у BingX стал причиной коллизий orderId между
пользователями с одинаковым символом/стороной). Используем positionId
напрямую.
"""

import hashlib
import hmac
import time
import asyncio
import logging
import contextvars
from urllib.parse import urlencode
import httpx

logger = logging.getLogger(__name__)

BASE_URL = 'https://contract.mexc.com'

MAX_RETRIES = 2
RETRY_DELAY = 1

_credentials_var: contextvars.ContextVar = contextvars.ContextVar('mexc_credentials', default=None)


def set_mexc_credentials(api_key: str, secret_key: str) -> None:
    _credentials_var.set((api_key or '', secret_key or ''))


def clear_mexc_credentials() -> None:
    _credentials_var.set(None)


def _get_credentials() -> tuple:
    creds = _credentials_var.get()
    if creds and creds[0] and creds[1]:
        return creds
    return ('', '')


def _to_bot_symbol(raw: str) -> str:
    """"BTC_USDT" -> "BTC-USDT"."""
    return (raw or '').replace('_', '-')


def _to_exchange_symbol(bot_symbol: str) -> str:
    """"BTC-USDT" -> "BTC_USDT"."""
    return (bot_symbol or '').replace('-', '_')


async def validate_keys(api_key: str, secret_key: str) -> dict:
    token = _credentials_var.set((api_key or '', secret_key or ''))
    try:
        return await get_balance()
    finally:
        _credentials_var.reset(token)


async def _request_with_retry(path: str, params: dict = None) -> dict:
    result = {}
    for attempt in range(MAX_RETRIES + 1):
        result = await _request(path, params)
        if not result.get('_transport_error'):
            return result
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    return result


async def _request(path: str, params: dict = None) -> dict:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    api_key, secret_key = _get_credentials()
    timestamp = str(int(time.time() * 1000))
    # GET — параметры сортируются по ключу и склеиваются в query string,
    # signString = accessKey + timestamp + parameterString, HMAC-SHA256
    # (см. официальную документацию MEXC Contract API, раздел "Signature").
    param_str = urlencode(sorted(params.items()))
    sign_str = f"{api_key}{timestamp}{param_str}"
    signature = hmac.new(secret_key.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {
        'ApiKey': api_key,
        'Request-Time': timestamp,
        'Signature': signature,
        'Content-Type': 'application/json',
    }
    url = BASE_URL + path
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return _transport_error('Unexpected response format')
            return data
    except httpx.HTTPError as e:
        return _transport_error(str(e))
    except ValueError as e:
        return _transport_error(f'Invalid JSON response: {e}')


def _transport_error(message: str) -> dict:
    # 'message' задублирован с 'error' намеренно — get_balance() и т.д.
    # читают именно 'message' (формат бизнес-ошибок MEXC), иначе
    # транспортная ошибка молча превращалась бы в бесполезное "Неизвестная
    # ошибка" (та же ошибка, что нашлась и исправлена в bybit_api.py).
    return {'error': message, 'message': message, 'success': False, '_transport_error': True}


async def get_balance() -> dict:
    result = await _request_with_retry('/api/v1/private/account/assets')
    if not result.get('success'):
        return {'success': False, 'error': result.get('message', 'Неизвестная ошибка'), 'code': result.get('code', -1)}
    assets = result.get('data') or []
    usdt = next((a for a in assets if a.get('currency') == 'USDT'), None)
    if not usdt:
        return {'success': False, 'error': 'USDT-актив не найден на аккаунте', 'code': -1}
    return {
        'success': True,
        'equity': float(usdt.get('equity') or 0),
        'available': float(usdt.get('availableBalance') or 0),
        'used_margin': float(usdt.get('positionMargin') or 0),
        'unrealized_pnl': float(usdt.get('unrealized') or 0),
        'currency': 'USDT',
    }


async def get_open_positions() -> dict:
    result = await _request_with_retry('/api/v1/private/position/open_positions')
    if not result.get('success'):
        return {'success': False, 'error': result.get('message', 'Неизвестная ошибка'), 'trades': []}

    positions = result.get('data') or []
    trades = []
    for pos in positions:
        hold_vol = float(pos.get('holdVol') or 0)
        if hold_vol == 0:
            continue
        symbol = _to_bot_symbol(pos.get('symbol', ''))
        # positionType: 1 = long, 2 = short (см. официальную документацию MEXC).
        side = 'LONG' if pos.get('positionType') == 1 else 'SHORT'
        trades.append({
            'orderId':       str(pos.get('positionId', f"{symbol}_{side}")),
            'symbol':        symbol,
            'side':          side,
            'entryPrice':    float(pos.get('openAvgPrice') or 0),
            'positionAmt':   hold_vol,
            'size':          hold_vol,
            'unrealizedPnl': float(pos.get('unRealizedPnl') or pos.get('unrealised') or 0),
            'leverage':      float(pos.get('leverage') or 1),
            # MEXC — не полный TP/SL по позиции в этом эндпоинте (частичные
            # TP/SL — отдельные условные ордера); оставляем None до
            # верификации на реальном аккаунте, как и SL/TP-фолбэк у
            # остальных адаптеров при недоступности данных.
            'stopLoss':      None,
            'takeProfit':    None,
            'status':        'OPEN',
        })
    return {'success': True, 'trades': trades}


async def get_closed_orders(symbol: str = '', limit: int = 20) -> dict:
    params = {'page_size': limit}
    if symbol:
        params['symbol'] = _to_exchange_symbol(symbol)
    result = await _request_with_retry('/api/v1/private/order/list/history_orders', params)
    if not result.get('success'):
        return {'success': False, 'error': result.get('message', 'Неизвестная ошибка'), 'trades': []}
    orders = result.get('data') or []
    closed = []
    for order in orders:
        # state: 3 = filled (заполнено) в терминологии MEXC contract API.
        if order.get('state') not in (3,):
            continue
        # side (1..4: open long/close short/open short/close long) — точную
        # семантику кодов MEXC стоит перепроверить на реальном ответе
        # (TODO verify); get_closed_orders (исполнения ордеров, не позиций)
        # не используется в history_import.py — там get_recent_closed_positions.
        closed.append({
            'orderId':     str(order.get('orderId', '')),
            'symbol':      _to_bot_symbol(order.get('symbol', '')),
            'side':        order.get('side'),
            'price':       float(order.get('dealAvgPrice') or order.get('price') or 0),
            'size':        float(order.get('dealVol') or 0),
            'realizedPnl': 0.0,
            'status':      str(order.get('state', '')),
            'time':        order.get('createTime', ''),
            'updateTime':  order.get('updateTime', ''),
        })
    return {'success': True, 'trades': closed}


async def get_recent_closed_positions(limit: int = 20) -> dict:
    """История ЗАКРЫТЫХ позиций — GET .../position/list/history_positions,
    единый вызов без обязательного symbol (в отличие от BingX/Bybit) —
    MEXC отдаёт полную историю позиций аккаунта сразу постранично."""
    result = await _request_with_retry(
        '/api/v1/private/position/list/history_positions', {'page_size': limit}
    )
    if not result.get('success'):
        return {'success': False, 'error': result.get('message', 'Неизвестная ошибка'), 'positions': []}

    raw = result.get('data') or []
    positions = []
    for p in raw:
        side = 'LONG' if p.get('positionType') == 1 else 'SHORT'
        positions.append({
            'positionId':   str(p.get('positionId', '')),
            'symbol':       _to_bot_symbol(p.get('symbol', '')),
            'side':         side,
            'entry_price':  float(p.get('openAvgPrice') or 0),
            'exit_price':   float(p.get('closeAvgPrice') or 0),
            'quantity':     float(p.get('closeVol') or 0),
            'realized_pnl': float(p.get('realised') or 0),
            'leverage':     float(p.get('leverage') or 1),
            'open_time':    p.get('createTime'),
            'close_time':   p.get('updateTime'),
        })

    positions.sort(key=lambda p: p.get('close_time') or 0, reverse=True)
    return {'success': True, 'positions': positions[:limit]}
