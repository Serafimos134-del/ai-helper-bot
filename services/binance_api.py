"""
services/binance_api.py
Binance USDT-M Futures (fapi) — реализация под Exchange Adapter Layer
(services/exchanges/binance.py), задача от 13.07.2026.

Структура и мультитенантный паттерн — см. services/bybit_api.py и docstring
там же (то же самое НЕ протестировано на реальном аккаунте, ключи будут
переданы после реализации).

Символы Binance — без разделителя ("BTCUSDT"), нормализуются в "BTC-USDT"
на выходе — см. bybit_api.py docstring, та же причина (единый market-data
поток через services/bingx_api.py для AI-контекста).

ВАЖНОЕ ОГРАНИЧЕНИЕ (в отличие от BingX/Bybit): у Binance Futures нет
готового эндпоинта "закрытые позиции" с парой цена-входа/цена-выхода —
API оперирует отдельными исполнениями ордеров (/fapi/v1/userTrades).
get_recent_closed_positions() реконструирует закрытые позиции из истории
сделок (см. _reconstruct_closed_positions) — стандартная техника для
Binance Futures (то же самое по сути делает и ccxt), но она рассчитана на
One-Way Mode (один нетто-объём на символ, направление не хранится отдельно
от знака объёма); Hedge Mode (одновременные LONG и SHORT на одном символе)
не поддерживается — сделки перепутались бы между двумя параллельными
позициями. Leverage не приходит в /userTrades вообще — берётся best-effort
из текущего /positionRisk по этому символу, если позиция ещё открыта,
иначе дефолт 1x.
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

BASE_URL = 'https://fapi.binance.com'

MAX_RETRIES = 2
RETRY_DELAY = 1

_credentials_var: contextvars.ContextVar = contextvars.ContextVar('binance_credentials', default=None)


def set_binance_credentials(api_key: str, secret_key: str) -> None:
    _credentials_var.set((api_key or '', secret_key or ''))


def clear_binance_credentials() -> None:
    _credentials_var.set(None)


def _get_credentials() -> tuple:
    creds = _credentials_var.get()
    if creds and creds[0] and creds[1]:
        return creds
    return ('', '')


def _to_bot_symbol(raw: str) -> str:
    if not raw:
        return raw
    if '-' in raw:
        return raw
    if raw.endswith('USDT'):
        return f"{raw[:-4]}-USDT"
    return raw


def _to_exchange_symbol(bot_symbol: str) -> str:
    return (bot_symbol or '').replace('-', '')


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
        if not (isinstance(result, dict) and result.get('_transport_error')):
            return result
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    return result


async def _request(path: str, params: dict = None):
    params = {k: v for k, v in (params or {}).items() if v is not None}
    api_key, secret_key = _get_credentials()
    params['timestamp'] = str(int(time.time() * 1000))
    params['recvWindow'] = 5000
    query_string = urlencode(params)
    signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    params['signature'] = signature
    headers = {'X-MBX-APIKEY': api_key}
    url = BASE_URL + path
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params, headers=headers)
            data = response.json()
            if response.status_code != 200:
                # Binance отдаёт {'code': -xxxx, 'msg': '...'} с HTTP-статусом
                # != 200 для бизнес-ошибок (неверный ключ, недостаточно прав
                # и т.п.) — не транспортная ошибка, ретраить не нужно.
                if isinstance(data, dict):
                    return data
                return {'code': -1, 'msg': 'Unexpected response format', '_transport_error': True}
            return data
    except httpx.HTTPError as e:
        return {'code': -1, 'msg': str(e), '_transport_error': True}
    except UnicodeError:
        # См. services/bybit_api.py:_request — та же причина (не-ASCII
        # символы в api_key/secret_key, httpx не может закодировать HTTP-
        # заголовок); UnicodeEncodeError — подкласс ValueError, без этой
        # ветки ловился бы ниже и подписывался как "Invalid JSON response".
        return {'code': -1, 'msg': 'API-ключ или секрет содержат недопустимые символы (не ASCII)', '_transport_error': True}
    except ValueError as e:
        return {'code': -1, 'msg': f'Invalid JSON response: {e}', '_transport_error': True}


async def get_balance() -> dict:
    result = await _request_with_retry('/fapi/v3/account')
    if isinstance(result, dict) and 'code' in result and result.get('code', 0) < 0:
        return {'success': False, 'error': result.get('msg', 'Неизвестная ошибка'), 'code': result.get('code', -1)}
    if not isinstance(result, dict) or 'totalMarginBalance' not in result:
        return {'success': False, 'error': 'Неожиданный формат ответа', 'code': -1}
    return {
        'success': True,
        'equity': float(result.get('totalMarginBalance') or 0),
        'available': float(result.get('availableBalance') or 0),
        'used_margin': float(result.get('totalInitialMargin') or 0),
        'unrealized_pnl': float(result.get('totalUnrealizedProfit') or 0),
        'currency': 'USDT',
    }


async def _get_symbol_leverage_map() -> dict:
    """symbol -> leverage из positionRisk (только для символов с открытой
    позицией сейчас) — /userTrades не отдаёт leverage вообще."""
    result = await _request_with_retry('/fapi/v3/positionRisk')
    if not isinstance(result, list):
        return {}
    return {p.get('symbol'): float(p.get('leverage') or 1) for p in result}


async def get_open_positions() -> dict:
    result = await _request_with_retry('/fapi/v3/positionRisk')
    if isinstance(result, dict) and result.get('code', 0) < 0:
        return {'success': False, 'error': result.get('msg', 'Неизвестная ошибка'), 'trades': []}
    if not isinstance(result, list):
        return {'success': False, 'error': 'Неожиданный формат ответа', 'trades': []}

    trades = []
    for pos in result:
        amt = float(pos.get('positionAmt') or 0)
        if amt == 0:
            continue
        symbol = _to_bot_symbol(pos.get('symbol', ''))
        side = 'LONG' if amt > 0 else 'SHORT'
        trades.append({
            'orderId':       f"{symbol}_{side}",
            'symbol':        symbol,
            'side':          side,
            'entryPrice':    float(pos.get('entryPrice') or 0),
            'positionAmt':   abs(amt),
            'size':          abs(amt),
            'unrealizedPnl': float(pos.get('unRealizedProfit') or 0),
            'leverage':      float(pos.get('leverage') or 1),
            'stopLoss':      None,
            'takeProfit':    None,
            'status':        'OPEN',
        })

    # Best-effort TP/SL из открытых ордеров — см. docstring модуля: Binance
    # мигрировал условные ордера (STOP_MARKET/TAKE_PROFIT_MARKET) на
    # отдельный Algo Order API у части аккаунтов, /fapi/v1/openOrders может
    # их не отдавать. Если это так — SL/TP останутся None (то же поведение,
    # что и при отсутствии SL/TP на бирже), сбой не критичен для остального
    # функционала. TODO verify на реальном аккаунте.
    try:
        orders_result = await _request_with_retry('/fapi/v1/openOrders')
        if isinstance(orders_result, list):
            tp_sl_map = {}
            for order in orders_result:
                sym = _to_bot_symbol(order.get('symbol', ''))
                order_type = order.get('type', '')
                try:
                    stop_price = float(order.get('stopPrice') or 0)
                except (TypeError, ValueError):
                    continue
                if not sym or stop_price <= 0:
                    continue
                tp_sl_map.setdefault(sym, {'takeProfit': None, 'stopLoss': None})
                if order_type in ('TAKE_PROFIT_MARKET', 'TAKE_PROFIT'):
                    tp_sl_map[sym]['takeProfit'] = stop_price
                elif order_type in ('STOP_MARKET', 'STOP'):
                    tp_sl_map[sym]['stopLoss'] = stop_price
            for t in trades:
                match = tp_sl_map.get(t['symbol'])
                if match:
                    t['stopLoss'] = match.get('stopLoss')
                    t['takeProfit'] = match.get('takeProfit')
    except Exception as e:
        logger.warning(f"get_open_positions (Binance): не удалось получить TP/SL из openOrders: {e}")

    return {'success': True, 'trades': trades}


async def get_closed_orders(symbol: str = '', limit: int = 20) -> dict:
    if not symbol:
        return {'success': False, 'error': 'Binance требует symbol для истории ордеров', 'trades': []}
    params = {'symbol': _to_exchange_symbol(symbol), 'limit': limit}
    result = await _request_with_retry('/fapi/v1/allOrders', params)
    if isinstance(result, dict) and result.get('code', 0) < 0:
        return {'success': False, 'error': result.get('msg', 'Неизвестная ошибка'), 'trades': []}
    if not isinstance(result, list):
        return {'success': False, 'error': 'Неожиданный формат ответа', 'trades': []}
    closed = []
    for order in result:
        if order.get('status') not in ('FILLED', 'CANCELED'):
            continue
        closed.append({
            'orderId':     order.get('orderId', ''),
            'symbol':      _to_bot_symbol(order.get('symbol', '')),
            'side':        order.get('side', ''),
            'price':       float(order.get('avgPrice') or 0),
            'size':        float(order.get('executedQty') or 0),
            'realizedPnl': 0.0,
            'status':      order.get('status', ''),
            'time':        order.get('time', ''),
            'updateTime':  order.get('updateTime', ''),
        })
    return {'success': True, 'trades': closed}


async def _get_user_trades(symbol: str, limit: int = 200) -> list:
    params = {'symbol': _to_exchange_symbol(symbol), 'limit': limit}
    result = await _request_with_retry('/fapi/v1/userTrades', params)
    if not isinstance(result, list):
        return []
    return sorted(result, key=lambda t: t.get('time', 0))


def _reconstruct_closed_positions(symbol_bot: str, trades: list, leverage: float) -> list:
    """Восстанавливает закрытые позиции из хронологической последовательности
    исполнений (см. docstring модуля — ограничение на One-Way Mode)."""
    positions = []
    pos_qty = 0.0
    pos_side = None
    entry_notional = entry_qty = 0.0
    exit_notional = exit_qty = 0.0
    realized = 0.0
    open_time = None
    close_time = None

    for t in trades:
        qty = float(t.get('qty') or 0)
        price = float(t.get('price') or 0)
        if qty <= 0:
            continue
        signed_qty = qty if t.get('side') == 'BUY' else -qty
        realized_pnl_trade = float(t.get('realizedPnl') or 0)

        if abs(pos_qty) < 1e-9:
            pos_side = 'LONG' if signed_qty > 0 else 'SHORT'
            entry_notional = entry_qty = exit_notional = exit_qty = realized = 0.0
            open_time = t.get('time')

        same_direction = (pos_qty >= 0 and signed_qty > 0) or (pos_qty <= 0 and signed_qty < 0)
        if same_direction:
            entry_notional += price * qty
            entry_qty += qty
        else:
            exit_notional += price * qty
            exit_qty += qty
            realized += realized_pnl_trade
            close_time = t.get('time')

        pos_qty += signed_qty

        if abs(pos_qty) < 1e-9 and exit_qty > 0:
            positions.append({
                'positionId':   f"{symbol_bot}_{open_time}",
                'symbol':       symbol_bot,
                'side':         pos_side,
                'entry_price':  entry_notional / entry_qty if entry_qty else 0,
                'exit_price':   exit_notional / exit_qty if exit_qty else 0,
                'quantity':     exit_qty,
                'realized_pnl': realized,
                'leverage':     leverage,
                'open_time':    open_time,
                'close_time':   close_time,
            })
            pos_qty = 0.0

    return positions


async def get_recent_closed_positions(limit: int = 20) -> dict:
    """См. docstring модуля — реконструкция из /userTrades, не готовый
    эндпоинт (в отличие от BingX/Bybit). Символы — открытые позиции +
    топ по объёму (единый market-data референс-фид BingX)."""
    from services.bingx_api import get_top_tickers

    open_res = await get_open_positions()
    open_symbols = {p['symbol'] for p in open_res.get('trades', [])} if open_res.get('success') else set()

    top_res = await get_top_tickers(30)
    top_symbols = {t.get('symbol') for t in top_res.get('tickers', [])} if top_res.get('success') else set()

    symbols = list((open_symbols | top_symbols) - {None, ''})
    if not symbols:
        return {'success': False, 'error': 'Не удалось получить список инструментов', 'positions': []}

    leverage_map = await _get_symbol_leverage_map()
    sem = asyncio.Semaphore(3)

    async def _fetch(sym_bot):
        async with sem:
            sym_exchange = _to_exchange_symbol(sym_bot)
            trades = await _get_user_trades(sym_bot)
            leverage = leverage_map.get(sym_exchange, 1)
            return _reconstruct_closed_positions(sym_bot, trades, leverage)

    results = await asyncio.gather(*[_fetch(s) for s in symbols], return_exceptions=True)
    all_positions = []
    for r in results:
        if isinstance(r, list):
            all_positions.extend(r)

    all_positions.sort(key=lambda p: p.get('close_time') or 0, reverse=True)
    return {'success': True, 'positions': all_positions[:limit]}
