"""
services/ai_decision_engine.py
AI Decision Engine — финальное решение на основе рыночной структуры.
Phase 5 фундамента для AI Market Analysis Engine.
"""

import logging
from services.structure_engine import analyze_structure
from services.stop_engine import analyze_stop
from services.tp_engine import analyze_tp

logger = logging.getLogger(__name__)


def analyze_decision(snapshot: dict, position: dict) -> dict:
    """
    Принимает market_snapshot и открытую позицию.
    Возвращает решение для трейдера:
    - decision: 'HOLD', 'EXIT', 'DCA', 'PARTIAL_TP', 'FULL_TP'
    - confidence: 'high', 'medium', 'low'
    - reason: пояснение
    - details: полные данные от всех движков
    """
    if not snapshot or not position:
        return {
            'decision': 'UNKNOWN',
            'confidence': 'low',
            'reason': 'Недостаточно данных',
            'details': {}
        }

    # Получаем данные от всех движков
    structure = analyze_structure(snapshot)
    stop_data = analyze_stop(snapshot, position)
    tp_data = analyze_tp(snapshot, position)

    current_price = snapshot.get('price', 0)
    side = position.get('side', 'LONG').upper()
    entry_price = float(position.get('entry_price', 0))
    quantity = float(position.get('quantity', 0))
    unrealized_pnl = float(position.get('unrealized_pnl', 0))

    if current_price <= 0 or entry_price <= 0:
        return {
            'decision': 'UNKNOWN',
            'confidence': 'low',
            'reason': 'Некорректные данные позиции',
            'details': {'structure': structure, 'stop': stop_data, 'tp': tp_data}
        }

    # Процент прибыли
    if quantity > 0:
        pnl_percent = (unrealized_pnl / (entry_price * quantity)) * 100
    else:
        pnl_percent = ((current_price - entry_price) / entry_price * 100) if side == 'LONG' else \
                       ((entry_price - current_price) / entry_price * 100)

    # Проверяем инвалидацию (EXIT)
    if stop_data.get('status') == 'exit':
        return {
            'decision': 'EXIT',
            'confidence': 'high',
            'reason': stop_data.get('reason', 'Идея сломана'),
            'details': {'structure': structure, 'stop': stop_data, 'tp': tp_data}
        }

    # Проверяем достижение TP1 (PARTIAL_TP)
    if tp_data.get('status') == 'tp1_near' and pnl_percent > 0:
        return {
            'decision': 'PARTIAL_TP',
            'confidence': 'high',
            'reason': f"Цена у TP1 (${tp_data['tp1']:.4f}), зафиксируйте {tp_data.get('partial_fix_pct', 25)}%",
            'details': {'structure': structure, 'stop': stop_data, 'tp': tp_data}
        }

    # Проверяем достижение TP2 (FULL_TP)
    if tp_data.get('status') == 'tp2_near' and pnl_percent > 0:
        return {
            'decision': 'FULL_TP',
            'confidence': 'high',
            'reason': f"Цена у TP2 (${tp_data['tp2']:.4f}), можно закрыть остаток",
            'details': {'structure': structure, 'stop': stop_data, 'tp': tp_data}
        }

    # Проверяем возможность DCA (добор)
    dca_count = int(position.get('dca_count', 0))
    max_dca = 2
    if dca_count < max_dca:
        trend = structure.get('trend', 'RANGING')
        if side == 'LONG' and trend == 'BULLISH' and pnl_percent > -5:
            # Цена снизилась к поддержке — можно добирать
            supports = structure.get('support_levels', [])
            if supports:
                nearest_support = max([s for s in supports if s < current_price], default=None)
                if nearest_support and abs(current_price - nearest_support) / current_price < 0.02:
                    return {
                        'decision': 'DCA',
                        'confidence': 'medium',
                        'reason': f"Цена у поддержки ${nearest_support:.4f}, можно добавить к позиции",
                        'details': {'structure': structure, 'stop': stop_data, 'tp': tp_data}
                    }
        elif side == 'SHORT' and trend == 'BEARISH' and pnl_percent > -5:
            resistances = structure.get('resistance_levels', [])
            if resistances:
                nearest_resistance = min([r for r in resistances if r > current_price], default=None)
                if nearest_resistance and abs(current_price - nearest_resistance) / current_price < 0.02:
                    return {
                        'decision': 'DCA',
                        'confidence': 'medium',
                        'reason': f"Цена у сопротивления ${nearest_resistance:.4f}, можно добавить к позиции",
                        'details': {'structure': structure, 'stop': stop_data, 'tp': tp_data}
                    }

    # Всё остальное — HOLD
    return {
        'decision': 'HOLD',
        'confidence': 'high',
        'reason': 'Тренд сохраняется, стоп не достигнут, TP далеко',
        'details': {'structure': structure, 'stop': stop_data, 'tp': tp_data}
    }