"""
services/market_data.py
Market Data Engine — получение и кэширование свечей (5m, 15m, 1h, 4h).
Phase 1 фундамента для AI Market Analysis Engine.
"""

import asyncio
import logging
from services.bingx_api import get_kline
from services.api_cache import api_cache

logger = logging.getLogger(__name__)

TIMEFRAMES = ['5m', '15m', '1h', '4h']
CANDLE_LIMITS = {
    '5m': 48,    # 4 часа
    '15m': 48,   # 12 часов
    '1h': 48,    # 2 дня
    '4h': 48,    # 8 дней
}


async def get_market_snapshot(symbol: str) -> dict:
    """
    Получает свечные данные для всех таймфреймов по символу.
    Возвращает словарь с ключами:
    - symbol
    - price (последняя цена)
    - candles_5m, candles_15m, candles_1h, candles_4h
    """
    if not symbol.endswith('-USDT'):
        symbol = f"{symbol}-USDT"

    tasks = {}
    for tf in TIMEFRAMES:
        cache_key = f"market_snapshot:{symbol}:{tf}"
        cached = await api_cache.get(cache_key)
        if cached:
            tasks[tf] = cached
        else:
            tasks[tf] = None  # будем заполнять ниже

    # Запрашиваем только те таймфреймы, которых нет в кэше
    fetch_tasks = []
    for tf in TIMEFRAMES:
        if tasks[tf] is None:
            fetch_tasks.append(tf)

    if fetch_tasks:
        logger.info(f"Запрашиваю свечи {symbol} для {fetch_tasks}")
        results = await asyncio.gather(*[
            get_kline(symbol, tf, CANDLE_LIMITS[tf]) for tf in fetch_tasks
        ])
        for tf, result in zip(fetch_tasks, results):
            if result.get('success'):
                klines = result.get('klines', [])
                tasks[tf] = klines
                # Кэшируем на 1 минуту (5m), 3 минуты (15m), 5 минут (1h), 10 минут (4h)
                ttl = {'5m': 60, '15m': 180, '1h': 300, '4h': 600}.get(tf, 300)
                await api_cache.set(f"market_snapshot:{symbol}:{tf}", klines, ttl=ttl)
            else:
                tasks[tf] = []

    # Последняя цена из 5m свечи (или из 15m если 5m пуст)
    price = None
    for tf in ['5m', '15m', '1h']:
        if tasks[tf] and len(tasks[tf]) > 0:
            last_candle = tasks[tf][-1]
            price = float(last_candle.get('close', last_candle.get('c', 0)))
            if price > 0:
                break

    return {
        'symbol': symbol,
        'price': price or 0,
        'candles_5m': tasks.get('5m', []),
        'candles_15m': tasks.get('15m', []),
        'candles_1h': tasks.get('1h', []),
        'candles_4h': tasks.get('4h', []),
    }