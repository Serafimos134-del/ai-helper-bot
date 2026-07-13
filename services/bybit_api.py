"""
services/bybit_api.py
Bybit V5 (Unified Trading Account, linear/USDT perpetual) — реализация под
Exchange Adapter Layer (services/exchanges/bybit.py), задача от 13.07.2026
("мультибиржевость — обязательная часть архитектуры").

Структура и мультитенантный паттерн (contextvars-изоляция ключей на
asyncio-таск) — намеренно повторяют services/bingx_api.py, единственный
уже проверенный на реальном трафике источник истины в проекте. Endpoints/
подпись сверены с официальной документацией Bybit V5
(bybit-exchange.github.io/docs/v5) на момент реализации, но НЕ протестированы
на реальном аккаунте (ключи будут переданы после реализации) — в отличие от
BingX-слоя, который уже прошёл боевую проверку. Места, отмеченные "TODO
verify", стоит перепроверить в первую очередь при появлении реальных ключей.

Символы Bybit — без разделителя ("BTCUSDT"). Остальной код (market data для
AI-контекста, calc_engine, БД) везде рассчитан на формат BingX "BTC-USDT" —
см. services/exchanges/base.py docstring: рыночные данные (котировки/свечи)
остаются единым потоком через services/bingx_api.py независимо от биржи
аккаунта пользователя. Поэтому все символы, отдаваемые наружу из этого
модуля, нормализуются в "BASE-USDT"; при обращении к самой Bybit — обратно
в "BASEUSDT".
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

BASE_URL = 'https://api.bybit.com'
RECV_WINDOW = '5000'

MAX_RETRIES = 2
RETRY_DELAY = 1

_credentials_var: contextvars.ContextVar = contextvars.ContextVar('bybit_credentials', default=None)


def set_bybit_credentials(api_key: str, secret_key: str) -> None:
    _credentials_var.set((api_key or '', secret_key or ''))


def clear_bybit_credentials() -> None:
    _credentials_var.set(None)


def _get_credentials() -> tuple:
    # Без .env-фолбэка — в отличие от BingX (services/bingx_api.py), у этого
    # модуля нет "владельца по умолчанию": Bybit доступен только
    # подписчикам, которые сами выбрали его при /setkeys. См. security-фикс
    # в bingx_api.py — почему тут нет неявного отката ни на что глобальное.
    creds = _credentials_var.get()
    if creds and creds[0] and creds[1]:
        return creds
    return ('', '')


def _to_bot_symbol(raw: str) -> str:
    """"BTCUSDT" -> "BTC-USDT" (формат остального кода, см. докстринг модуля)."""
    if not raw:
        return raw
    if '-' in raw:
        return raw
    if raw.endswith('USDT'):
        return f"{raw[:-4]}-USDT"
    return raw


def _to_exchange_symbol(bot_symbol: str) -> str:
    """"BTC-USDT" -> "BTCUSDT"."""
    return (bot_symbol or '').replace('-', '')


async def validate_keys(api_key: str, secret_key: str) -> dict:
    token = _credentials_var.set((api_key or '', secret_key or ''))
    try:
        return await get_balance()
    finally:
        _credentials_var.reset(token)


def _sign(api_key: str, secret_key: str, timestamp: str, param_str: str) -> str:
    sign_str = f"{timestamp}{api_key}{RECV_WINDOW}{param_str}"
    return hmac.new(secret_key.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()


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
    timestamp = str(int(time.time() * 1000))
    api_key, secret_key = _get_credentials()
    # GET — query string, отсортированная по ключу (urlencode(sorted(...))
    # — тот же подход, что уже используется в bingx_api.py:_sign).
    query_string = urlencode(sorted(params.items()))
    signature = _sign(api_key, secret_key, timestamp, query_string)
    headers = {
        'X-BAPI-API-KEY': api_key,
        'X-BAPI-SIGN': signature,
        'X-BAPI-TIMESTAMP': timestamp,
        'X-BAPI-RECV-WINDOW': RECV_WINDOW,
        'Content-Type': 'application/json',
    }
    url = BASE_URL + path
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params, headers=headers)
            # Раньше raise_for_status() вызывался ДО чтения тела ответа —
            # если Bybit при non-2xx статусе всё же прислал JSON с retCode/
            # retMsg (у части ошибок это так, HTTP-статус и бизнес-код не
            # взаимоисключающие), этот JSON терялся целиком, и пользователь
            # видел только обёртку httpx ("Client error '401 ...' for url
            # ..."), а не реальный текст Bybit — вместо диагностики ещё один
            # уровень непрозрачности (найдено на реальном тесте с ключами).
            try:
                data = response.json()
            except ValueError:
                data = None
            if response.status_code != 200:
                if isinstance(data, dict) and (data.get('retMsg') or data.get('retCode') is not None):
                    return data
                return _transport_error(f"HTTP {response.status_code}: {response.text[:300] or response.reason_phrase}")
            if not isinstance(data, dict):
                return _transport_error('Unexpected response format')
            return data
    except httpx.HTTPError as e:
        return _transport_error(str(e))
    except UnicodeError:
        # UnicodeEncodeError — подкласс ValueError, поэтому без отдельной
        # ветки ловился бы ниже и подписывался как "Invalid JSON response",
        # хотя проблема на самом деле в том, что api_key/secret_key
        # содержат не-ASCII символы (httpx не может закодировать их в
        # HTTP-заголовок) — см. _looks_like_key в handlers/onboarding.py,
        # это основная точка защиты, здесь просто честное сообщение на
        # случай обхода той проверки другим вызывающим кодом.
        return _transport_error('API-ключ или секрет содержат недопустимые символы (не ASCII)')
    except ValueError as e:
        return _transport_error(f'Invalid JSON response: {e}')


def _transport_error(message: str) -> dict:
    # Ключ 'retMsg' задублирован с 'error' намеренно — get_balance()/
    # get_open_positions() и т.д. читают именно 'retMsg' (формат обычных
    # бизнес-ошибок Bybit), иначе транспортная ошибка (сеть/прокси/HTTP-
    # статус) молча превращалась бы в бесполезное "Неизвестная ошибка" для
    # пользователя — баг, из-за которого реальная причина отказа /setkeys
    # терялась (найдено на реальном тесте с ключами Bybit).
    return {'error': message, 'retMsg': message, 'retCode': -1, '_transport_error': True}


async def get_balance() -> dict:
    result = await _request_with_retry('/v5/account/wallet-balance', {'accountType': 'UNIFIED'})
    if result.get('retCode') == 0:
        accounts = (result.get('result') or {}).get('list') or []
        if not accounts:
            return {'success': False, 'error': 'Пустой ответ по балансу (аккаунт не Unified?)', 'code': -1}
        acc = accounts[0]
        return {
            'success': True,
            'equity': float(acc.get('totalEquity') or 0),
            'available': float(acc.get('totalAvailableBalance') or 0),
            'used_margin': float(acc.get('totalInitialMargin') or 0),
            'unrealized_pnl': float(acc.get('totalPerpUPL') or 0),
            'currency': 'USDT',
        }
    return {'success': False, 'error': result.get('retMsg', 'Неизвестная ошибка'), 'code': result.get('retCode', -1)}


async def get_open_positions() -> dict:
    # settleCoin=USDT обязателен при запросе без symbol (category=linear
    # поддерживает и USDT-, и USDC-маржинальные контракты) — иначе Bybit
    # либо требует явный symbol, либо трактует settleCoin неоднозначно
    # (см. известные issue у ccxt на эту тему).
    result = await _request_with_retry('/v5/position/list', {'category': 'linear', 'settleCoin': 'USDT'})
    if result.get('retCode') != 0:
        return {'success': False, 'error': result.get('retMsg', 'Неизвестная ошибка'), 'trades': []}

    positions = (result.get('result') or {}).get('list') or []
    trades = []
    for pos in positions:
        size = float(pos.get('size') or 0)
        if size == 0:
            continue
        symbol = _to_bot_symbol(pos.get('symbol', ''))
        side = 'LONG' if pos.get('side') == 'Buy' else 'SHORT'
        position_id = f"{symbol}_{side}"
        tp = pos.get('takeProfit')
        sl = pos.get('stopLoss')
        trades.append({
            'orderId':       position_id,
            'symbol':        symbol,
            'side':          side,
            'entryPrice':    float(pos.get('avgPrice') or 0),
            'positionAmt':   size,
            'size':          size,
            'unrealizedPnl': float(pos.get('unrealisedPnl') or 0),
            'leverage':      float(pos.get('leverage') or 1),
            'stopLoss':      float(sl) if sl and float(sl) > 0 else None,
            'takeProfit':    float(tp) if tp and float(tp) > 0 else None,
            'status':        'OPEN',
        })
    return {'success': True, 'trades': trades}


async def get_closed_orders(symbol: str = '', limit: int = 20) -> dict:
    """Исполнения ордеров (не позиций) — GET /v5/order/history."""
    params = {'category': 'linear', 'limit': limit}
    if symbol:
        params['symbol'] = _to_exchange_symbol(symbol)
    result = await _request_with_retry('/v5/order/history', params)
    if result.get('retCode') != 0:
        return {'success': False, 'error': result.get('retMsg', 'Неизвестная ошибка'), 'trades': []}
    orders = (result.get('result') or {}).get('list') or []
    closed = []
    for order in orders:
        if order.get('orderStatus') not in ('Filled', 'Cancelled'):
            continue
        closed.append({
            'orderId':     order.get('orderId', ''),
            'symbol':      _to_bot_symbol(order.get('symbol', '')),
            'side':        order.get('side', ''),
            'price':       float(order.get('avgPrice') or 0),
            'size':        float(order.get('cumExecQty') or 0),
            'realizedPnl': 0.0,  # order-history не отдаёт PnL по ордеру — см. get_recent_closed_positions
            'status':      order.get('orderStatus', ''),
            'time':        order.get('createdTime', ''),
            'updateTime':  order.get('updatedTime', ''),
        })
    return {'success': True, 'trades': closed}


async def get_position_closed_pnl(symbol: str, limit: int = 50) -> dict:
    """Закрытые ПОЗИЦИИ по одному символу — GET /v5/position/closed-pnl
    (symbol обязателен, как и у BingX positionHistory — нет вызова
    "все инструменты сразу", см. get_recent_closed_positions)."""
    params = {'category': 'linear', 'symbol': _to_exchange_symbol(symbol), 'limit': limit}
    result = await _request_with_retry('/v5/position/closed-pnl', params)
    if result.get('retCode') != 0:
        return {'success': False, 'error': result.get('retMsg', 'Неизвестная ошибка'), 'positions': []}
    raw = (result.get('result') or {}).get('list') or []
    positions = []
    for p in raw:
        side = 'LONG' if p.get('side') == 'Buy' else 'SHORT'
        positions.append({
            'positionId':   p.get('orderId', ''),
            'symbol':       _to_bot_symbol(p.get('symbol', '')),
            'side':         side,
            'entry_price':  float(p.get('avgEntryPrice') or 0),
            'exit_price':   float(p.get('avgExitPrice') or 0),
            'quantity':     float(p.get('qty') or 0),
            'realized_pnl': float(p.get('closedPnl') or 0),
            'leverage':     float(p.get('leverage') or 1),
            'open_time':    p.get('createdTime'),
            'close_time':   p.get('updatedTime'),
        })
    return {'success': True, 'positions': positions}


async def get_recent_closed_positions(limit: int = 20) -> dict:
    """Best-effort сбор недавних закрытых позиций по нескольким инструментам
    — тот же подход, что и services/bingx_api.py:get_recent_closed_positions
    (closed-pnl требует symbol на каждый вызов): опрашиваем символы текущих
    открытых позиций + топ по объёму (референс-фид BingX, единый для всех
    бирж — см. docstring services/exchanges/base.py)."""
    from services.bingx_api import get_top_tickers

    open_res = await get_open_positions()
    open_symbols = {p['symbol'] for p in open_res.get('trades', [])} if open_res.get('success') else set()

    top_res = await get_top_tickers(30)
    top_symbols = {t.get('symbol') for t in top_res.get('tickers', [])} if top_res.get('success') else set()

    symbols = list((open_symbols | top_symbols) - {None, ''})
    if not symbols:
        return {'success': False, 'error': 'Не удалось получить список инструментов', 'positions': []}

    sem = asyncio.Semaphore(3)

    async def _fetch(sym):
        async with sem:
            return await get_position_closed_pnl(sym)

    results = await asyncio.gather(*[_fetch(s) for s in symbols], return_exceptions=True)
    all_positions = []
    for r in results:
        if isinstance(r, dict) and r.get('success'):
            all_positions.extend(r['positions'])

    all_positions.sort(key=lambda p: p.get('close_time') or 0, reverse=True)
    return {'success': True, 'positions': all_positions[:limit]}
