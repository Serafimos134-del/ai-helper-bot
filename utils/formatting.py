"""
utils/formatting.py
Общие функции форматирования вывода для Telegram-сообщений.
Раньше format_verdict была продублирована дословно в core/router.py и
services/auto_sync.py — вынесена сюда, чтобы у обоих был один источник правды.
"""

import json
from ai.engines.structure_arbiter import get_structure_override


def format_verdict(verdict_raw) -> str:
    """Форматирует JSON-вердикт JudgeAgent в читаемую строку с эмодзи."""
    try:
        verdict = json.loads(verdict_raw) if isinstance(verdict_raw, str) else verdict_raw
        verdict_text    = verdict.get('verdict', '—')
        final_score     = verdict.get('final_score', '—')
        verdict_summary = verdict.get('summary', '')
        warnings        = verdict.get('warnings', [])
        emoji_map = {'STRONG_ENTER': '🟢', 'ENTER': '🟢', 'WAIT': '🟡', 'AVOID': '🔴'}
        emoji = emoji_map.get(verdict_text, '⚪')
        result = f"{emoji} {verdict_text} ({final_score}/100)"
        if verdict_summary:
            result += f"\n{verdict_summary}"
        if warnings:
            result += "\n⚠️ " + " | ".join(warnings)
        return result
    except Exception:
        return str(verdict_raw)


# Русские подписи для компонентов TradeScorer.score()['details'] — сопоставлены
# с пунктами Этапа 5 плана AI Trading Core ("качество входа", "соблюдение
# риск-менеджмента" и т.п.), чтобы разбор закрытой сделки был понятен без
# знания внутренних имён полей.
_SCORE_LABELS = {
    'rr_ratio': 'Вход (Risk/Reward)',
    'leverage': 'Плечо',
    'risk_per_trade': 'Риск на сделку',
    'discipline': 'Дисциплина (стоп/тейк)',
    'psychology': 'Сопровождение/психология',
}


def format_score_breakdown(score: dict) -> str:
    """Форматирует детальную оценку TradeScorer.score()/score_open_position()
    (итоговый score 0-10 + разбор по компонентам) в читаемый текст."""
    if not score:
        return ""
    total = score.get('total_score', '—')
    verdict = score.get('verdict', '')
    lines = [f"📊 Оценка сделки: {total}/10 — {verdict}"]
    details = score.get('details', {})
    for key, label in _SCORE_LABELS.items():
        if key in details:
            lines.append(f"• {label}: {details[key]}/10")
    return "\n".join(lines)


_DECISION_EMOJI = {
    'HOLD': '✅', 'EXIT': '🚪', 'DCA': '➕',
    'PARTIAL_TP': '💰', 'FULL_TP': '🏁',
}


def format_position_plan(plan: dict, header: str = None) -> str:
    """Форматирует position_plan (ai_decision_engine.analyze_decision(), см.
    ai/engines/structure_arbiter.py:build_structure_plan) в читаемый текст.
    Общая точка для ответа /consilium (Этап 4) и проактивных уведомлений о
    сопровождении сделки (Этап 7, core/scheduler.py:position_watch_job).

    С Варианта C (см. DECISION_FLOW_AUDIT.md) position_plan сам по себе
    больше не независимый вердикт — JudgeAgent.synthesize() уже учёл эти
    сигналы (override при EXIT/FULL_TP, структурный компонент скора для
    остального). Заголовок здесь показывается как "Решение по позиции"
    только когда plan.decision — тот же override, что уже применён в
    вердикте Judge (get_structure_override), иначе — как вспомогательная
    "Структурная деталь", чтобы не выглядело как второе, конкурирующее
    решение рядом с вердиктом Judge."""
    if not plan or not plan.get('decision') or plan['decision'] == 'UNKNOWN':
        return ""

    decision = plan['decision']
    emoji = _DECISION_EMOJI.get(decision, '❔')
    if get_structure_override(plan):
        decision_line = f"{emoji} Решение по позиции: {decision}"
    else:
        decision_line = f"{emoji} Структурная деталь (не решение Judge): {decision}"
    lines = [f"{header}" if header else None,
             decision_line,
             plan.get('reason', '')]

    details = plan.get('details', {})
    stop = details.get('stop', {})
    tp_data = details.get('tp', {})

    # hard_sl/tp1/tp2 — расчётные уровни от structure_engine (по
    # поддержкам/сопротивлениям), а не факт того, что SL/TP выставлены на
    # бирже (это отдельно — блок "🛑 SL"/"🎯 TP" в handlers/ai.py, из
    # position.stop_loss/take_profit). Раньше оба блока выглядели
    # противоречиво в одном сообщении: "стоп не установлен" и тут же
    # "Hard SL: $X" без пометки, что второе — рекомендация, а не факт.
    if stop.get('hard_sl'):
        lines.append(f"🛑 Рекомендуемый Hard SL (инвалидация): ${stop['hard_sl']:.4f}")
    if stop.get('status') not in (None, 'keep', 'exit') and stop.get('recommended_sl'):
        lines.append(f"🔧 Перенести стоп на: ${stop['recommended_sl']:.4f} ({stop.get('reason', '')})")
    if tp_data.get('tp1'):
        tp_line = f"🎯 Рекомендуемый TP1: ${tp_data['tp1']:.4f}"
        if tp_data.get('tp2'):
            tp_line += f" | TP2: ${tp_data['tp2']:.4f}"
        lines.append(tp_line)
    if (stop.get('hard_sl') or tp_data.get('tp1')) and not (plan.get('_real_sl') or plan.get('_real_tp')):
        lines.append("⚠️ На бирже SL/TP не выставлены — выше только расчётные уровни")

    return "\n".join(l for l in lines if l)
