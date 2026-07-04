"""
services/tp_engine.py
AI TP Engine — автоматический расчёт тейк-профитов на основе рыночной структуры.
Phase 4 фундамента для AI Market Analysis Engine.
"""

import logging
from services.structure_engine import analyze_structure

logger = logging.getLogger(__name__)


def analyze_tp(snapshot: dict, position: dict) -> dict:
    """
    Принимает market_snapshot и открытую позицию.
    Возвращает:
    - tp1: первая зона тейк-профита (ближайшее сопротивление / поддержка)
    - tp2: вторая зона (следующее сопротивление / поддержка)
    - runner: зона для удержания остатка позиции
    - partial_fix_pct: рекомендованный процент фиксации на TP1 (20-30)
    - status: 'tp1_near', 'tp2_near', 'runner', 'hold'
    """
    structure = analyze_structure(snapshot)
    current_price = snapshot.get('price', 0)
    if current_price <= 0:
        return {'tp1': None, 'tp2': None, 'runner': None, 'partial_fix_pct': 20, 'status': 'unknown'}

    side = position.get('side', 'LONG').upper()
    tp_zones_manual = position.get('tp_zones')  # ручные зоны, если заданы

    # Если трейдер вручную задал TP-зоны через /setidea, используем их
    if tp_zones_manual:
        try:
            import json
            zones = json.loads(tp_zones_manual) if isinstance(tp_zones_manual, str) else tp_zones_manual
            if isinstance(zones, list) and len(zones) >= 2:
                return {
                    'tp1': zones[0],
                    'tp2': zones[1],
                    'runner': zones[-1] if len(zones) > 2 else zones[1],
                    'partial_fix_pct': 25,
                    'status': 'manual'
                }
            elif isinstance(zones, list) and len(zones) == 1:
                return {
                    'tp1': zones[0],
                    'tp2': None,
                    'runner': None,
                    'partial_fix_pct': 25,
                    'status': 'manual'
                }
        except (json.JSONDecodeError, TypeError):
            pass

    # Автоматический расчёт на основе структуры
    if side == 'LONG':
        resistances = structure.get('resistance_levels', [])
        # Ближайшие сопротивления выше текущей цены
        targets = sorted([r for r in resistances if r > current_price * 1.002])
        
        tp1 = targets[0] if len(targets) >= 1 else current_price * 1.03
        tp2 = targets[1] if len(targets) >= 2 else current_price * 1.06
        runner = targets[-1] if len(targets) >= 3 else current_price * 1.10
    else:  # SHORT
        supports = structure.get('support_levels', [])
        # Ближайшие поддержки ниже текущей цены
        targets = sorted([s for s in supports if s < current_price * 0.998], reverse=True)
        
        tp1 = targets[0] if len(targets) >= 1 else current_price * 0.97
        tp2 = targets[1] if len(targets) >= 2 else current_price * 0.94
        runner = targets[-1] if len(targets) >= 3 else current_price * 0.90

    # Определяем статус: насколько близко цена к TP1
    if tp1:
        distance_pct = abs(current_price - tp1) / current_price * 100
        if distance_pct < 1.0:
            status = 'tp1_near'
        elif distance_pct < 3.0:
            status = 'tp2_near'
        else:
            status = 'hold'
    else:
        status = 'hold'

    return {
        'tp1': round(tp1, 4) if tp1 else None,
        'tp2': round(tp2, 4) if tp2 else None,
        'runner': round(runner, 4) if runner else None,
        'partial_fix_pct': 25,
        'status': status
    }