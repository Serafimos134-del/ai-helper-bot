"""
services/stop_engine.py
AI Stop Engine — автоматический расчёт стоп-лоссов на основе рыночной структуры.
Phase 3 фундамента для AI Market Analysis Engine.
"""

import logging
from services.structure_engine import analyze_structure

logger = logging.getLogger(__name__)


def analyze_stop(snapshot: dict, position: dict) -> dict:
    """
    Принимает market_snapshot и открытую позицию.
    Возвращает:
    - hard_sl: минимальный уровень, где идея ломается
    - recommended_sl: предлагаемый уровень стопа (БУ, профит-протекшн, трейлинг)
    - status: 'keep', 'breakeven', 'profit_protection', 'trailing', 'exit'
    - reason: пояснение
    """
    structure = analyze_structure(snapshot)
    current_price = snapshot.get('price', 0)
    if current_price <= 0:
        return {'hard_sl': None, 'recommended_sl': None, 'status': 'unknown', 'reason': 'Нет цены'}

    entry_price = float(position.get('entry_price', 0))
    side = position.get('side', 'LONG').upper()
    unrealized_pnl = float(position.get('unrealized_pnl', 0))
    invalidation_sl = position.get('invalidation_sl')  # ручная инвалидация, если задана
    quantity = float(position.get('quantity', 0))

    if entry_price <= 0:
        return {'hard_sl': None, 'recommended_sl': None, 'status': 'unknown', 'reason': 'Нет цены входа'}

    # Определяем hard_sl (идея ломается)
    if invalidation_sl:
        hard_sl = float(invalidation_sl)
    else:
        # используем структурный уровень: для лонга – ближайшая поддержка, для шорта – ближайшее сопротивление
        if side == 'LONG':
            supports = structure.get('support_levels', [])
            hard_sl = max([s for s in supports if s < current_price], default=entry_price * 0.95)
        else:
            resistances = structure.get('resistance_levels', [])
            hard_sl = min([r for r in resistances if r > current_price], default=entry_price * 1.05)

    # Процент прибыли
    if quantity > 0:
        pnl_percent = (unrealized_pnl / (entry_price * quantity)) * 100
    else:
        pnl_percent = ((current_price - entry_price) / entry_price * 100) if side == 'LONG' else \
                       ((entry_price - current_price) / entry_price * 100)

    # Выбор recommended_sl в зависимости от PnL
    if side == 'LONG':
        if pnl_percent <= 0:
            recommended_sl = hard_sl
            status = 'keep'
            reason = "Держать стоп на уровне инвалидации"
        elif 0 < pnl_percent <= 15:
            recommended_sl = entry_price  # безубыток
            status = 'breakeven'
            reason = "Перевести стоп в безубыток"
        elif 15 < pnl_percent <= 30:
            # подтягиваем стоп под последний локальный минимум (из структуры)
            lows = [s for s in structure.get('support_levels', []) if s < current_price]
            recommended_sl = max(lows) if lows else entry_price * 1.01
            status = 'profit_protection'
            reason = "Защита прибыли, стоп подтянут"
        else:  # > 30%
            # трейлинг: стоп на уровне последней поддержки, но не ниже БУ + 5%
            lows = [s for s in structure.get('support_levels', []) if s < current_price]
            recommended_sl = max(lows) if lows else entry_price * 1.05
            if recommended_sl <= entry_price * 1.05:
                recommended_sl = entry_price * 1.05  # минимум 5% защиты
            status = 'trailing'
            reason = "Трейлинг-стоп, защита прибыли"
    else:  # SHORT
        if pnl_percent <= 0:
            recommended_sl = hard_sl
            status = 'keep'
            reason = "Держать стоп на уровне инвалидации"
        elif 0 < pnl_percent <= 15:
            recommended_sl = entry_price  # безубыток
            status = 'breakeven'
            reason = "Перевести стоп в безубыток"
        elif 15 < pnl_percent <= 30:
            highs = [r for r in structure.get('resistance_levels', []) if r > current_price]
            recommended_sl = min(highs) if highs else entry_price * 0.99
            status = 'profit_protection'
            reason = "Защита прибыли, стоп подтянут"
        else:
            highs = [r for r in structure.get('resistance_levels', []) if r > current_price]
            recommended_sl = min(highs) if highs else entry_price * 0.95
            if recommended_sl >= entry_price * 0.95:
                recommended_sl = entry_price * 0.95
            status = 'trailing'
            reason = "Трейлинг-стоп, защита прибыли"

    # Проверка: если текущая цена прошла hard_sl, сигнализируем EXIT
    if (side == 'LONG' and current_price <= hard_sl) or (side == 'SHORT' and current_price >= hard_sl):
        status = 'exit'
        reason = "Цена достигла уровня инвалидации, идея сломана"

    return {
        'hard_sl': round(hard_sl, 4),
        'recommended_sl': round(recommended_sl, 4),
        'status': status,
        'reason': reason,
        'pnl_percent': round(pnl_percent, 2)
    }