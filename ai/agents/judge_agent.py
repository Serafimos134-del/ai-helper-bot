import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.trader_context import compute_dna_adjustment
from ai.risk_profile import compute_risk_profile_adjustment
from ai.engines.structure_arbiter import get_structure_override, structure_score

logger = logging.getLogger(__name__)


class JudgeAgent:
    """Финальный арбитр с детерминированной логикой, без LLM."""

    WEIGHTS = {
        "market": 0.35,
        "risk": 0.35,
        "psychology": 0.15,
        "trade": 0.15,
    }

    # Веса для mode='open' с доступным position_plan (см.
    # DECISION_FLOW_AUDIT.md, Вариант C, требование 4) — structure перестаёт
    # быть отдельным вердиктом и становится компонентом общего скора.
    # Используется только когда structure_score(position_plan) вернул не
    # None; иначе (post_trade/setup/нет данных структуры) поведение не
    # меняется — применяется обычный WEIGHTS.
    OPEN_WEIGHTS_WITH_STRUCTURE = {
        "market": 0.30,
        "risk": 0.30,
        "psychology": 0.15,
        "trade": 0.10,
        "structure": 0.15,
    }

    THRESHOLDS = {
        "STRONG_ENTER": 85,
        "ENTER": 70,
        "WAIT": 55,
    }

    def __init__(self, provider: BaseProvider = None):
        self.provider = provider          # больше не используется

    async def synthesize(self, market_json: str, risk_json: str, psychology_json: str,
                         mode: str = None, trade_score: int = None,
                         confidence: float = None, disagreement: float = None,
                         trader_context: dict = None, position_plan: dict = None,
                         risk_profile: dict = None) -> str:
        try:
            market = json.loads(market_json) if isinstance(market_json, str) else market_json
        except json.JSONDecodeError:
            market = {"market_score": 50}
        try:
            risk = json.loads(risk_json) if isinstance(risk_json, str) else risk_json
        except json.JSONDecodeError:
            risk = {"risk_score": 50}
        try:
            psychology = json.loads(psychology_json) if isinstance(psychology_json, str) else psychology_json
        except json.JSONDecodeError:
            psychology = {"psychology_score": 50}

        market_score = self._extract_score(market, "market_score")
        risk_score = self._extract_score(risk, "risk_score")
        psychology_score = self._extract_score(psychology, "psychology_score")

        if trade_score is not None:
            final_trade_score = int(trade_score)
        else:
            final_trade_score = self._extract_score(market, "market_score", default=50) * 0.5

        # Position Analyst / Trade Management (ai_decision_engine) как
        # специализированный аналитик внутри Judge, а не отдельный вердикт
        # (см. DECISION_FLOW_AUDIT.md, Вариант C, требование 4). struct_score
        # — None, если структура недоступна (mode != 'open' или нет данных) —
        # тогда веса не меняются, поведение идентично прежнему.
        struct_score = structure_score(position_plan) if mode == 'open' else None
        weights = self.OPEN_WEIGHTS_WITH_STRUCTURE if struct_score is not None else self.WEIGHTS

        final_score = (
            market_score * weights["market"] +
            risk_score * weights["risk"] +
            psychology_score * weights["psychology"] +
            final_trade_score * weights["trade"]
        )
        if struct_score is not None:
            final_score += struct_score * weights["structure"]
        final_score = int(max(0, min(100, final_score)))
        base_score = final_score

        if confidence is None:
            scores = [market_score, risk_score, psychology_score]
            raw_disagreement = max(scores) - min(scores)
            confidence = max(20, 100 - raw_disagreement)
        if disagreement is None:
            scores = [market_score, risk_score, psychology_score]
            disagreement = max(scores) - min(scores)

        # TraderContext (advisory-only, см. TRADER_INTELLIGENCE_ARCHITECTURE.md,
        # §7 и §9): ограниченная по модулю поправка на основе личной истории
        # трейдера — активна только при достаточной выборке
        # (compute_dna_adjustment сам это проверяет и возвращает active=False,
        # если данных мало). Применяется here, ДО определения verdict, чтобы
        # поправка реально могла сдвинуть решение, а не быть косметикой
        # поверх уже готового ответа (см. §1.3/§5 архитектурного документа —
        # именно так выглядела старая, нерабочая версия персонализации).
        dna_adjustment = compute_dna_adjustment(trader_context)
        if dna_adjustment["active"] and dna_adjustment["score_delta"] != 0:
            final_score = int(max(0, min(100, final_score + dna_adjustment["score_delta"])))

        # Персональная модель риска (задача от 12.07.2026, ai/risk_profile.py)
        # — тот же advisory-паттерн, что и dna_adjustment выше: ограниченная
        # поправка на основе фактического Risk Score пользователя (не
        # заявленного профиля — тот используется только для сравнения
        # заявленный/фактический в handlers/risk_profile.py, не здесь).
        risk_profile_adjustment = compute_risk_profile_adjustment(risk_profile)
        if risk_profile_adjustment["active"] and risk_profile_adjustment["score_delta"] != 0:
            final_score = int(max(0, min(100, final_score + risk_profile_adjustment["score_delta"])))

        # Жёсткий override (DECISION_FLOW_AUDIT.md, Вариант C, требование 3):
        # пробой инвалидации или достижение полного TP — объективный факт
        # рынка, не мнение, которое можно перевесить скором. Форсирует
        # verdict независимо от final_score. Та же функция
        # (get_structure_override) используется в core/scheduler.py:
        # position_watch_job(), поэтому ручной запрос и проактивное
        # сопровождение не могут разойтись в этих случаях.
        structure_override = get_structure_override(position_plan) if mode == 'open' else None
        if structure_override:
            verdict = structure_override["verdict"]
        else:
            verdict = self._get_verdict(final_score, mode)

        warnings = []
        # ScoringEngine.calc_risk отдаёт скор безопасности в диапазоне ~50-100
        # (100 = SL/TP выставлены и разумное плечо, 50 = худший случай: нет
        # защитных ордеров + плечо ≥10x). Порог 70 ловит реально рискованные
        # комбинации, не срабатывая на единичных мелких минусах.
        if risk_score < 70:
            warnings.append("Высокий риск")
        if psychology_score < 40:
            warnings.append("Психологическая нестабильность")
        if disagreement > 40:
            warnings.append("Сильное расхождение мнений агентов")
        if dna_adjustment["active"] and dna_adjustment["score_delta"] != 0:
            warnings.append(
                f"Персональная поправка {dna_adjustment['score_delta']:+d}: {dna_adjustment['reason']}"
            )
        if risk_profile_adjustment["active"] and risk_profile_adjustment["score_delta"] != 0:
            warnings.append(
                f"Риск-профиль {risk_profile_adjustment['score_delta']:+d}: {risk_profile_adjustment['reason']}"
            )
        if structure_override:
            warnings.insert(0, f"⚡ Принудительное решение: {structure_override['reason']}")

        summary = self._generate_summary(final_score, verdict, confidence, disagreement, mode, structure_override)

        result = {
            "final_score": final_score,
            "base_score": base_score,
            "dna_adjustment": dna_adjustment,
            "risk_profile_adjustment": risk_profile_adjustment,
            "structure_score": struct_score,
            "structure_override": structure_override,
            "verdict": verdict,
            "confidence": confidence,
            "warnings": warnings,
            "summary": summary,
            "scores": {
                "market": market_score,
                "risk": risk_score,
                "psychology": psychology_score,
                "trade": final_trade_score,
            }
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    @classmethod
    def _get_verdict(cls, score: int, mode: str = None) -> str:
        if mode in ('open', 'post_trade'):
            if score >= 70:
                return "HOLD" if mode == 'open' else "GOOD_TRADE"
            elif score >= 55:
                return "HOLD"
            else:
                return "CLOSE" if mode == 'open' else "BAD_TRADE"
        for verdict, threshold in cls.THRESHOLDS.items():
            if score >= threshold:
                return verdict
        return "AVOID"

    @classmethod
    def _generate_summary(cls, score: int, verdict: str, confidence: float, disagreement: float,
                          mode: str = None, structure_override: dict = None) -> str:
        if structure_override:
            if structure_override["decision"] == "EXIT":
                base = f"Позицию необходимо закрыть — {structure_override['reason']}."
            elif structure_override["decision"] == "FULL_TP":
                base = f"Цель по позиции достигнута — {structure_override['reason']}. Рекомендуется зафиксировать прибыль."
            else:
                base = f"Позицию рекомендуется закрыть — {structure_override['reason']}."
        elif mode == 'open':
            if verdict == 'HOLD':
                base = "Позицию рекомендуется удерживать."
            elif verdict == 'CLOSE':
                base = "Позицию рекомендуется закрыть."
            else:
                base = f"Решение по позиции неопределённое (вердикт: {verdict})."
        elif mode == 'post_trade':
            if verdict == 'GOOD_TRADE':
                base = "Сделка качественная, соблюдены риск-менеджмент и дисциплина."
            elif verdict == 'BAD_TRADE':
                base = "Сделка неудачная, есть проблемы в управлении риском или психологии."
            else:
                base = f"Оценка сделки: {verdict}."
        else:
            verdict_text = {
                "STRONG_ENTER": "Сильный сигнал на вход.",
                "ENTER": "Вход допустим.",
                "WAIT": "Рекомендуется подождать.",
                "AVOID": "Вход не рекомендуется.",
            }
            base = verdict_text.get(verdict, "Решение не определено.")

        if confidence < 0.6:
            base += f" Уверенность низкая ({confidence:.0%})."
        elif disagreement > 0.3:
            base += f" Есть расхождения между агентами ({disagreement:.0%})."

        return base

    @staticmethod
    def _extract_score(data: dict, key: str, default: int = 50) -> int:
        if key in data:
            return int(data[key])
        metrics = data.get("metrics", {})
        if key in metrics:
            return int(metrics[key])
        alt_keys = ["score", "final_score", "total_score"]
        for alt in alt_keys:
            if alt in data:
                return int(data[alt])
        return default