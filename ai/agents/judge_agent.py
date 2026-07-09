import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.trader_context import compute_dna_adjustment

logger = logging.getLogger(__name__)


class JudgeAgent:
    """Финальный арбитр с детерминированной логикой, без LLM."""

    WEIGHTS = {
        "market": 0.35,
        "risk": 0.35,
        "psychology": 0.15,
        "trade": 0.15,
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
                         trader_context: dict = None) -> str:
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

        final_score = (
            market_score * self.WEIGHTS["market"] +
            risk_score * self.WEIGHTS["risk"] +
            psychology_score * self.WEIGHTS["psychology"] +
            final_trade_score * self.WEIGHTS["trade"]
        )
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

        summary = self._generate_summary(final_score, verdict, confidence, disagreement, mode)

        result = {
            "final_score": final_score,
            "base_score": base_score,
            "dna_adjustment": dna_adjustment,
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
                          mode: str = None) -> str:
        if mode == 'open':
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