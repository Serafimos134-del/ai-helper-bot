"""
ai/engines/structure_arbiter.py
Единая точка арбитража между JudgeAgent и ai_decision_engine (см.
DECISION_FLOW_AUDIT.md, Вариант C, утверждён пользователем). Раньше
ai_decision_engine.analyze_decision() был вторым, независимым вердиктом
(HOLD/EXIT/DCA/PARTIAL_TP/FULL_TP) рядом с вердиктом JudgeAgent
(HOLD/CLOSE) — оба могли расходиться в одном ответе, а position_watch_job
вообще не спрашивал Judge. Теперь:

- position_plan (структурные сигналы: инвалидация/TP/DCA/тренд) строится
  ОДИН РАЗ, здесь — и используется и ConsensusEngine.analyze_open_position()
  (фид для JudgeAgent), и core/scheduler.py:position_watch_job()
  (проактивное сопровождение), чтобы оба пути видели один и тот же факт.
- Жёсткие структурные факты (пробой инвалидации, достижение полного TP) —
  override, форсирующий итоговый вердикт независимо от взвешенного скора.
  Это одна и та же функция для ручного /consilium и для watch_job, поэтому
  расхождение в этих случаях структурно невозможно.
- Остальные сигналы (PARTIAL_TP, DCA-возможность, статус стопа/тренд) не
  форсируют вердикт — они становятся компонентом скора JudgeAgent
  (structure_score), как и рекомендовано в требовании 4 задачи.
"""

import logging
from typing import Optional

from ai.engines.normalizer import normalize_position
from services.market_data import get_market_snapshot
from services.ai_decision_engine import analyze_decision

logger = logging.getLogger(__name__)

# Структурные решения ai_decision_engine, которые считаются объективным
# фактом рынка, а не мнением — при них форсируем итоговый вердикт Judge
# независимо от взвешенного скора. Список ограничен ровно тем, что
# analyze_decision() уже умеет объективно определять (см.
# DECISION_FLOW_AUDIT.md, Шаг 3, Вариант C, требование 3):
#   - EXIT     — цена прошла уровень инвалидации (hard_sl пробит);
#   - FULL_TP  — цена у TP2, цель по позиции достигнута.
# PARTIAL_TP/DCA сознательно НЕ входят в override — это входные сигналы
# для скора Judge (требование 4), не факт "идея сломана/цель достигнута".
OVERRIDE_DECISIONS = {
    'EXIT': 'CLOSE',
    'FULL_TP': 'CLOSE',
}

# Структурные статусы stop_engine → базовый вклад в structure_score.
_STOP_STATUS_BASE_SCORE = {
    'trailing': 85,            # прибыль защищается трейлингом — сильная позиция
    'profit_protection': 75,   # стоп подтянут в плюс
    'breakeven': 65,           # стоп в безубытке — риска по сделке уже нет
    'keep': 50,                # нейтрально — идея ещё не подтверждена и не сломана
}
_TP_PROXIMITY_BONUS = {
    'tp1_near': 15,
    'tp2_near': 25,  # обычно уже перехвачено override; бонус — на случай,
                      # если override почему-то не применился (defensive)
}
_DCA_OPPORTUNITY_BONUS = 10


async def build_structure_plan(raw_position: dict) -> dict:
    """Единственное место, где считается position_plan. raw_position —
    позиция как она пришла с BingX API/из БД, до normalize_position()."""
    symbol = raw_position.get("symbol", "")
    if not symbol:
        return {}
    try:
        normalized = normalize_position(raw_position)
        # Trade Manager v2 поля не приходят из BingX API — пробрасываем их
        # с исходного объекта, если они там были (например, позиция из
        # db.get_open_trades()).
        normalized["dca_count"] = raw_position.get("dca_count", 0)
        normalized["invalidation_sl"] = raw_position.get("invalidation_sl")
        normalized["tp_zones"] = raw_position.get("tp_zones")

        snapshot = await get_market_snapshot(symbol)
        plan = analyze_decision(snapshot, normalized)
        # hard_sl/tp1/tp2 в plan — расчётные уровни от structure_engine, не
        # факт того, что на бирже реально стоит SL/TP (см. AUDIT.md,
        # раздел про побочную находку 2026-07-09) — помечаем явно, чтобы
        # форматирование могло честно это показать.
        plan["_real_sl"] = bool(normalized.get("stop_loss"))
        plan["_real_tp"] = bool(normalized.get("take_profit"))
        return plan
    except Exception as e:
        logger.warning(f"build_structure_plan: не удалось построить план для {symbol}: {e}")
        return {}


def get_structure_override(plan: Optional[dict]) -> Optional[dict]:
    """Возвращает {'verdict', 'decision', 'reason'}, если plan содержит
    override-факт, иначе None. Используется и JudgeAgent.synthesize(), и
    core/scheduler.py:position_watch_job() — одна и та же функция, чтобы
    ручной запрос и проактивное сопровождение не могли разойтись
    (DECISION_FLOW_AUDIT.md, требование 5)."""
    if not plan:
        return None
    decision = plan.get('decision')
    forced_verdict = OVERRIDE_DECISIONS.get(decision)
    if not forced_verdict:
        return None
    return {
        'verdict': forced_verdict,
        'decision': decision,
        'reason': plan.get('reason', ''),
    }


def structure_score(plan: Optional[dict]) -> Optional[float]:
    """Немаркерные структурные сигналы → вход в скор JudgeAgent (0-100).
    None, если структура недоступна (нет позиции/данных) — компонент
    тогда не участвует в весах вовсе, а не подставляется нейтральным
    значением молча (тот же принцип честности, что и в остальном
    AI Trading Core, см. TRADER_DNA_V1.md §1.1/Фаза 3)."""
    if not plan or not plan.get('details'):
        return None
    details = plan['details']
    stop = details.get('stop', {})
    tp = details.get('tp', {})

    score = _STOP_STATUS_BASE_SCORE.get(stop.get('status'), 50)
    score += _TP_PROXIMITY_BONUS.get(tp.get('status'), 0)
    if plan.get('decision') == 'DCA':
        score += _DCA_OPPORTUNITY_BONUS

    return max(0.0, min(100.0, score))


def format_sl_tp_block(pos: dict, position_plan: Optional[dict]) -> str:
    """Exchange SL/TP (факт биржи) vs Recommended SL/TP (расчётные уровни
    AI Core от structure_engine) — явное разделение для текстов Risk/
    Psychology-агентов (см. аудит источников данных Risk Agent). Только
    для объяснения: НЕ используется для risk_score/psychology_score —
    наличие рекомендации не является фактом защиты позиции на бирже,
    числовые скора продолжают штрафовать отсутствие реального SL/TP как
    раньше."""
    exchange_sl = pos.get('stop_loss')
    exchange_tp = pos.get('take_profit')

    recommended_sl = None
    recommended_tp1 = None
    if position_plan:
        details = position_plan.get('details', {})
        recommended_sl = details.get('stop', {}).get('hard_sl')
        recommended_tp1 = details.get('tp', {}).get('tp1')

    lines = [f"Exchange SL: {f'{float(exchange_sl):.4f}' if exchange_sl else 'отсутствует'}"]
    if recommended_sl:
        lines.append(f"Recommended SL: {recommended_sl:.4f}")
    lines.append("")
    lines.append(f"Exchange TP: {f'{float(exchange_tp):.4f}' if exchange_tp else 'отсутствует'}")
    if recommended_tp1:
        lines.append(f"Recommended TP1: {recommended_tp1:.4f}")

    return "\n".join(lines)
