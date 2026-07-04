"""
services/structure_engine.py
Structure Engine — определяет тренд, поддержки/сопротивления, рыночные зоны.
Phase 2 фундамента для AI Market Analysis Engine.
"""

import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ─── helpers ────────────────────────────────────────────────

def _extract_ohlc(candle) -> tuple:
    """Извлекает (open, high, low, close) из свечи BingX."""
    if isinstance(candle, dict):
        o = float(candle.get('open', candle.get('o', 0)))
        h = float(candle.get('high', candle.get('h', 0)))
        l = float(candle.get('low', candle.get('l', 0)))
        c = float(candle.get('close', candle.get('c', 0)))
        return o, h, l, c
    return 0.0, 0.0, 0.0, 0.0


def _swings(highs, lows, window: int = 3) -> tuple:
    """
    Находит свинг-точки:
    - high_swings: свечи, чей high больше всех соседей в окне window
    - low_swings: свечи, чей low меньше всех соседей в окне window
    Возвращает индексы свингов.
    """
    n = len(highs)
    if n < window * 2 + 1:
        return [], []

    high_swings = []
    low_swings = []
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            high_swings.append(i)
        if lows[i] == min(lows[i - window : i + window + 1]):
            low_swings.append(i)
    return high_swings, low_swings


def _cluster_levels(levels: List[float], tolerance: float = 0.005) -> List[float]:
    """Группирует близкие уровни (в %) и возвращает средние значения кластеров."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters = []
    current_cluster = [levels[0]]

    for lvl in levels[1:]:
        if abs(lvl - current_cluster[-1]) / current_cluster[-1] <= tolerance:
            current_cluster.append(lvl)
        else:
            clusters.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [lvl]
    clusters.append(sum(current_cluster) / len(current_cluster))
    return clusters


# ─── основной движок ────────────────────────────────────────

def analyze_structure(snapshot: dict) -> dict:
    """
    Принимает market_snapshot (результат get_market_snapshot).
    Возвращает:
    - trend: 'BULLISH', 'BEARISH', 'RANGING'
    - support_levels: список цен поддержки
    - resistance_levels: список цен сопротивления
    - structure: список 'HH', 'HL', 'LH', 'LL' (Higher High и т.п.)
    """
    # Используем 1h свечи как основной таймфрейм для структуры
    candles = snapshot.get('candles_1h', [])
    if len(candles) < 20:
        # fallback на 15m
        candles = snapshot.get('candles_15m', [])
    if len(candles) < 20:
        # fallback на 5m
        candles = snapshot.get('candles_5m', [])
    if len(candles) < 10:
        return {
            'trend': 'UNKNOWN',
            'support_levels': [],
            'resistance_levels': [],
            'structure': [],
        }

    highs = []
    lows = []
    closes = []
    for c in candles:
        o, h, l, close = _extract_ohlc(c)
        highs.append(h)
        lows.append(l)
        closes.append(close)

    # 1. Тренд по SMA (простая скользящая средняя)
    n = len(closes)
    sma_short = sum(closes[-9:]) / min(9, n) if n >= 9 else sum(closes) / n
    sma_long = sum(closes[-20:]) / min(20, n) if n >= 20 else sma_short
    current_price = closes[-1]

    if current_price > sma_short > sma_long:
        trend = 'BULLISH'
    elif current_price < sma_short < sma_long:
        trend = 'BEARISH'
    else:
        trend = 'RANGING'

    # 2. Свинги и уровни
    high_swings_idx, low_swings_idx = _swings(highs, lows, window=3)

    resistance_raw = [highs[i] for i in high_swings_idx]
    support_raw = [lows[i] for i in low_swings_idx]

    # Оставляем уровни вблизи текущей цены (±5%)
    if current_price > 0:
        resistance_raw = [r for r in resistance_raw if r > current_price * 1.001]
        support_raw = [s for s in support_raw if s < current_price * 0.999]

    resistance_levels = _cluster_levels(resistance_raw, tolerance=0.008)
    support_levels = _cluster_levels(support_raw, tolerance=0.008)

    # 3. Структура (HH, HL, LH, LL) по свингам
    structure = []
    swing_highs = [highs[i] for i in high_swings_idx]
    swing_lows = [lows[i] for i in low_swings_idx]

    # Упрощённый анализ последних 4 свингов
    if len(swing_highs) >= 2:
        if swing_highs[-1] > swing_highs[-2]:
            structure.append('HH')
        else:
            structure.append('LH')
    if len(swing_lows) >= 2:
        if swing_lows[-1] > swing_lows[-2]:
            structure.append('HL')
        else:
            structure.append('LL')

    return {
        'trend': trend,
        'support_levels': sorted(support_levels, reverse=True),
        'resistance_levels': sorted(resistance_levels),
        'structure': structure,
    }