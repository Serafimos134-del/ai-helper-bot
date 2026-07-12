import hashlib
import hmac
import time
import asyncio
import os
import logging
import contextvars
from urllib.parse import urlencode
import httpx
from services.api_cache import api_cache

logger = logging.getLogger(__name__)

BINGX_API_KEY = os.getenv('BINGX_API_KEY', '')
BINGX_SECRET_KEY = os.getenv('BINGX_SECRET_KEY', '')
BASE_URL = 'https://open-api.bingx.com'

MAX_RETRIES = 2
RETRY_DELAY = 1

# Мультитенантность (см. MULTITENANCY_MIGRATION_PLAN.md, Этап 1): ключи
# конкретного пользователя для текущего asyncio-таска. Каждый Telegram-
# апдейт обрабатывается в своём Task (core/user_context.py — middleware,
# группа -1, вызывается раньше всех остальных хендлеров), поэтому
# contextvars корректно изолируют ключи разных пользователей при
# конкурентных запросах — без протаскивания api_key/secret_key параметром
# через ContextBuilder/AIOrchestrator/ConsensusEngine (5+ слоёв вызовов).
# Фоновые джобы (auto_sync_job/position_watch_job) устанавливают ключи
# сами на каждой итерации по пользователю. Если контекст не установлен
# (например, старые/ещё не мигрированные пути) — используется глобальный
# fallback из .env, чтобы не сломать текущий single-user режим.
_credentials_var: contextvars.ContextVar = contextvars.ContextVar('bingx_credentials', default=None)


def set_bingx_credentials(api_key: str, secret_key: str) -> None:
    """Устанавливает BingX-ключи текущего пользователя для этого asyncio-таска."""
    _credentials_var.set((api_key or '', secret_key or ''))


def clear_bingx_credentials() -> None:
    _credentials_var.set(None)


def _get_credentials() -> tuple:
    creds = _credentials_var.get()
    if creds and creds[0] and creds[1]:
        return creds
    return (BINGX_API_KEY, BINGX_SECRET_KEY)


def _get_timestamp() -> str:
    return str(int(time.time() * 1000))


def _sign(params: dict) -> str:
    _, secret_key = _get_credentials()
    query_string = urlencode(sorted(params.items()))
    signature = hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


async def _request_with_retry(method: str, path: str, params: dict = None) -> dict:
    # Ретраим только транспортные/сетевые сбои (см. _transport_error в _request),
    # а не бизнес-ошибки биржи (недостаточно маржи, неверные параметры и т.п.) —
    # те могут случайно совпасть с code=-1 и раньше тоже ретраились бы, что не нужно.
    result = {}
    for attempt in range(MAX_RETRIES + 1):
        result = await _request(method, path, params)
        if not result.get('_transport_error'):
            return result
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    return result


async def _request(method: str, path: str, params: dict = None) -> dict:
    if params is None:
        params = {}
    params['timestamp'] = _get_timestamp()
    params['signature'] = _sign(params)
    api_key, _ = _get_credentials()
    headers = {
        'X-BX-APIKEY': api_key,
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
                return {'error': 'Unexpected response format', 'code': -1, '_transport_error': True, 'raw': data}
            return data
    except httpx.HTTPError as e:
        return {'error': str(e), 'code': -1, '_transport_error': True}
    except ValueError as e:
        return {'error': f'Invalid JSON response: {e}', 'code': -1, '_transport_error': True}


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
    """Возвращает список открытых позиций с актуальными стоп‑лоссами и тейк‑профитами."""
    path = '/openApi/swap/v2/user/positions'
    result = await _request_with_retry('GET', path)

    if result.get('code') != 0:
        return {
            'success': False,
            'error': result.get('msg', 'Неизвестная ошибка'),
            'trades': []
        }

    positions = result.get('data', [])
    if not isinstance(positions, list):
        positions = positions.get('positions', []) if isinstance(positions, dict) else []

    trades = []
    for pos in positions:
        amt = float(pos.get('positionAmt', 0))
        if amt == 0:
            continue
        symbol = pos.get('symbol', '')
        side = 'LONG' if amt > 0 else 'SHORT'
        position_id = f"{symbol}_{side}"
        trades.append({
            'orderId':       position_id,
            'symbol':        symbol,
            'side':          side,
            'entryPrice':    float(pos.get('avgPrice', 0)),
            'positionAmt':   abs(amt),
            'size':          abs(amt),
            'unrealizedPnl': float(pos.get('unrealizedProfit', 0)),
            'leverage':      pos.get('leverage', 1),
            'stopLoss':      None,
            'takeProfit':    None,
            'status':        'OPEN',
        })

    # ------------------------------------------------------------
    # Дополнительно получаем открытые ордера, чтобы подтянуть TP/SL
    try:
        orders_result = await _request_with_retry('GET', '/openApi/swap/v2/trade/openOrders')
        if orders_result.get('code') == 0:
            open_orders = orders_result.get('data', {}).get('orders', [])
            # Словарь для быстрого поиска. Ключ зависит от режима аккаунта
            # (см. ниже) — либо "SYMBOL_LONG"/"SYMBOL_SHORT" (Hedge Mode),
            # либо просто "SYMBOL" (One-Way Mode).
            tp_sl_map = {}
            for order in open_orders:
                sym = order.get('symbol', '')
                pos_side = order.get('positionSide', '')  # LONG/SHORT (Hedge) или BOTH (One-Way)
                order_type = order.get('type', '')

                # order.get('stopPrice', 0) отдаёт дефолт только если ключа
                # нет вовсе — если ключ есть, но значение "" (пустая
                # строка, реальный случай для некоторых ордеров, например
                # "Т-п/с-л Вся позиция"), float('') кидает ValueError.
                # Раньше это исключение ловилось ВНЕШНИМ try/except вокруг
                # всего цикла — из-за одного такого ордера обрывалась
                # обработка вообще всех ордеров (и уже обработанные до него
                # результаты не терялись, но все последующие — не
                # обрабатывались никогда), поэтому TP/SL не подтягивался
                # вообще ни для одной позиции, на каждом синке, независимо
                # от типа ордера или режима аккаунта (см. AUDIT.md, SOL-USDT).
                try:
                    stop_price = float(order.get('stopPrice') or 0)
                except (TypeError, ValueError):
                    logger.warning(
                        f"get_open_positions: некорректный stopPrice у ордера "
                        f"{sym} type={order_type} positionSide={pos_side}: "
                        f"{order.get('stopPrice')!r}. Полный ордер: {order}"
                    )
                    continue

                if not sym or not pos_side or stop_price <= 0:
                    continue

                # BingX в One-Way Mode (сейчас default-режим для новых
                # аккаунтов) всегда возвращает positionSide="BOTH" для
                # ордеров, независимо от реальной стороны позиции — тогда
                # как сторона самой позиции (ниже, side) считается из
                # знака positionAmt и всегда LONG/SHORT. Раньше ключ
                # ордера строился напрямую из positionSide ("SYMBOL_BOTH"),
                # который никогда не совпадал с ключом позиции
                # ("SYMBOL_LONG"/"SYMBOL_SHORT") — SL/TP реально
                # существовал на бирже, но терялся при сопоставлении. В
                # One-Way Mode на символ бывает только одна позиция, поэтому
                # сопоставление по одному символу однозначно; в Hedge Mode
                # positionSide ордера — реальная сторона, сопоставляем как
                # раньше (по символу+стороне, чтобы не перепутать
                # одновременные LONG и SHORT на одном символе).
                key = sym if pos_side == 'BOTH' else f"{sym}_{pos_side}"
                if key not in tp_sl_map:
                    tp_sl_map[key] = {'takeProfit': None, 'stopLoss': None}

                # BingX поддерживает и market-, и limit-триггерные варианты
                # TP/SL (TAKE_PROFIT_MARKET/TAKE_PROFIT, STOP_MARKET/STOP) —
                # раньше проверялись только _MARKET-варианты, из-за чего
                # реально выставленный лимитный TP/SL (например, через
                # "частичный TP/SL" в приложении BingX) не подтягивался, и
                # Risk/Psychology-агенты видели позицию как незащищённую,
                # хотя стоп/тейк на бирже стоял.
                if order_type in ('TAKE_PROFIT_MARKET', 'TAKE_PROFIT'):
                    tp_sl_map[key]['takeProfit'] = stop_price
                elif order_type in ('STOP_MARKET', 'STOP'):
                    tp_sl_map[key]['stopLoss'] = stop_price

            # Применяем к trades. Заранее не знаем режим аккаунта (API это
            # явно не отдаёт) — проверяем оба варианта ключа; реально
            # заполнен будет только один из них, так как ордер приходит
            # либо с positionSide="BOTH" (One-Way), либо с реальной
            # стороной (Hedge), не с обоими вариантами одновременно.
            for t in trades:
                match = tp_sl_map.get(f"{t['symbol']}_{t['side']}") or tp_sl_map.get(t['symbol'])
                if match:
                    t['stopLoss'] = match.get('stopLoss')
                    t['takeProfit'] = match.get('takeProfit')

    except Exception as e:
        # Если не удалось получить ордера – оставляем null (SL/TP будут None)
        logger.warning(f"get_open_positions: не удалось получить TP/SL из openOrders: {e}")

    return {'success': True, 'trades': trades}


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
                    'orderId':     order.get('orderId', ''),
                    'symbol':      order.get('symbol', ''),
                    'side':        order.get('side', ''),
                    'price':       float(order.get('avgPrice', 0)),
                    'size':        float(order.get('executedQty', 0)),
                    'realizedPnl': float(order.get('profit', 0)),
                    'status':      order.get('status', ''),
                    'time':        order.get('time', ''),
                    'updateTime':  order.get('updateTime', '')
                })
        return {'success': True, 'trades': closed}
    else:
        return {
            'success': False,
            'error': result.get('msg', 'Неизвестная ошибка'),
            'trades': []
        }


async def get_top_tickers(limit: int = 10) -> dict:
    cache_key = f"top_tickers:{limit}"
    cached = await api_cache.get(cache_key)
    if cached:
        return cached
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
        result = {'success': True, 'tickers': sorted_tickers[:limit]}
    else:
        result = {'success': False, 'error': data.get('error', 'Unknown error'), 'tickers': []}
    await api_cache.set(cache_key, result)
    return result


async def get_kline(symbol: str = "BTC-USDT", interval: str = "1h", limit: int = 24) -> dict:
    cache_key = f"kline:{symbol}:{interval}:{limit}"
    cached = await api_cache.get(cache_key)
    if cached:
        return cached
    url = f"{BASE_URL}/openApi/swap/v3/quote/klines"
    params = {'symbol': symbol, 'interval': interval, 'limit': limit}
    data = await _public_request_with_retry(url, params)
    if isinstance(data, dict) and data.get('code') == 0:
        klines = data.get('data', [])
        if not isinstance(klines, list):
            result = {'success': False, 'error': 'Unexpected format', 'klines': []}
        else:
            result = {'success': True, 'klines': klines}
    else:
        result = {'success': False, 'error': data.get('error', 'Unknown error'), 'klines': []}
    await api_cache.set(cache_key, result)
    return result


async def get_ticker(symbol: str) -> dict:
    cache_key = f"ticker:{symbol}"
    cached = await api_cache.get(cache_key)
    if cached:
        return cached
    url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
    params = {'symbol': symbol}
    data = await _public_request_with_retry(url, params)
    if isinstance(data, dict) and data.get('code') == 0:
        ticker_data = data.get('data', {})
        if isinstance(ticker_data, list):
            if not ticker_data:
                result = {'success': False, 'error': 'Symbol not found', 'ticker': {}}
            else:
                result = {'success': True, 'ticker': ticker_data[0]}
        elif isinstance(ticker_data, dict):
            result = {'success': True, 'ticker': ticker_data}
        else:
            result = {'success': False, 'error': 'Unexpected format', 'ticker': {}}
    else:
        result = {'success': False, 'error': data.get('error', 'Unknown error'), 'ticker': {}}
    await api_cache.set(cache_key, result)
    return result


async def get_funding_rate(symbol: str) -> dict:
    cache_key = f"funding:{symbol}"
    cached = await api_cache.get(cache_key)
    if cached:
        return cached
    url = f"{BASE_URL}/openApi/swap/v2/quote/premiumIndex"
    params = {'symbol': symbol}
    data = await _public_request_with_retry(url, params)
    if isinstance(data, dict) and data.get('code') == 0:
        result_data = data.get('data', {})
        if isinstance(result_data, list) and result_data:
            result_data = result_data[0]
        result = {
            'success': True,
            'funding_rate': float(result_data.get('lastFundingRate', 0)),
            'mark_price':   float(result_data.get('markPrice', 0)),
            'index_price':  float(result_data.get('indexPrice', 0)),
        }
    else:
        result = {'success': False, 'error': data.get('error', 'Unknown error')}
    await api_cache.set(cache_key, result)
    return result


async def get_open_interest(symbol: str) -> dict:
    cache_key = f"oi:{symbol}"
    cached = await api_cache.get(cache_key)
    if cached:
        return cached
    url = f"{BASE_URL}/openApi/swap/v2/quote/openInterest"
    params = {'symbol': symbol}
    data = await _public_request_with_retry(url, params)
    if isinstance(data, dict) and data.get('code') == 0:
        result_data = data.get('data', {})
        result = {
            'success': True,
            'open_interest': float(result_data.get('openInterest', 0)),
        }
    else:
        result = {'success': False, 'error': data.get('error', 'Unknown error')}
    await api_cache.set(cache_key, result)
    return result


def _calculate_atr(klines: list, period: int = 14) -> float:
    if len(klines) < period + 1:
        return 0.0
    true_ranges = []
    for i in range(1, len(klines)):
        prev = klines[i-1]
        curr = klines[i]
        close_prev = float(prev.get('close', 0))
        high_cur   = float(curr.get('high', 0))
        low_cur    = float(curr.get('low', 0))
        tr = max(high_cur - low_cur,
                 abs(high_cur - close_prev),
                 abs(low_cur  - close_prev))
        true_ranges.append(tr)
    if not true_ranges:
        return 0.0
    return sum(true_ranges[-period:]) / period


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