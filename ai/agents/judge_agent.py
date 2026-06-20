import logging
import json
from ai.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class JudgeAgent:
    """Финальный арбитр с взвешенной score matrix и опциональным LLM‑объяснением."""

    # Веса агентов в финальном решении
    WEIGHTS = {
        "market": 0.35,
        "risk": 0.35,
        "psychology": 0.15,
        "trade": 0.15,
    }

    # Пороги для вердикта
    THRESHOLDS = {
        "STRONG_ENTER": 85,
        "ENTER": 70,
        "WAIT": 55,
        # ниже 55 → AVOID
    }

    def __init__(self, provider: BaseProvider = None):
        self.provider = provider  # опционален, для LLM‑объяснения

    def synthesize(self, market_json: str, risk_json: str, psychology_json: str) -> str:
        """
        Принимает JSON‑выводы трёх агентов и возвращает финальный вердикт.
        """
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

        # 1. Извлекаем скоринг агентов (нормированный к 0–100)
        market_score = self._extract_score(market, "market_score")
        risk_score = self._extract_score(risk, "risk_score")
        psychology_score = self._extract_score(psychology, "psychology_score")

        # 2. Trade score (пока упрощённо: 50 = нейтрально, позже можно подключить TradeScorer)
        trade_score = self._extract_score(market, "market_score", default=50) * 0.5  # временно

        # 3. Score matrix
        final_score = (
            market_score * self.WEIGHTS["market"] +
            risk_score * self.WEIGHTS["risk"] +
            psychology_score * self.WEIGHTS["psychology"] +
            trade_score * self.WEIGHTS["trade"]
        )
        final_score = int(max(0, min(100, final_score)))

        # 4. Вердикт
        verdict = self._get_verdict(final_score)

        # 5. Confidence (на основе разброса мнений)
        scores = [market_score, risk_score, psychology_score]
        disagreement = max(scores) - min(scores)
        confidence = max(20, 100 - disagreement)

        # 6. Warnings
        warnings = []
        if risk_score < 40:
            warnings.append("Высокий риск")
        if psychology_score < 40:
            warnings.append("Психологическая нестабильность")
        if disagreement > 40:
            warnings.append("Сильное расхождение мнений агентов")

        # 7. Summary
        summary = self._generate_summary(final_score, verdict, confidence, disagreement)

        # 8. Опциональное LLM‑объяснение
        if self.provider:
            try:
                enhanced = self._enhance_with_llm(final_score, verdict, confidence, market_score, risk_score, psychology_score)
                if enhanced:
                    summary = enhanced
            except Exception as e:
                logger.error(f"LLM enhancement failed: {e}")

        result = {
            "final_score": final_score,
            "verdict": verdict,
            "confidence": confidence,
            "warnings": warnings,
            "summary": summary,
            "scores": {
                "market": market_score,
                "risk": risk_score,
                "psychology": psychology_score,
                "trade": trade_score,
            }
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    @staticmethod
    def _extract_score(data: dict, key: str, default: int = 50) -> int:
        """Извлекает скор из структуры агента."""
        # Может быть как на верхнем уровне, так и вложенным
        if key in data:
            return int(data[key])
        # Ищем в metrics
        metrics = data.get("metrics", {})
        if key in metrics:
            return int(metrics[key])
        # Пробуем альтернативные ключи
        alt_keys = ["score", "final_score", "total_score"]
        for alt in alt_keys:
            if alt in data:
                return int(data[alt])
        return default

    @classmethod
    def _get_verdict(cls, score: int) -> str:
        for verdict, threshold in cls.THRESHOLDS.items():
            if score >= threshold:
                return verdict
        return "AVOID"

    @classmethod
    def _generate_summary(cls, score: int, verdict: str, confidence: int, disagreement: int) -> str:
        """Генерирует текстовое summary без LLM."""
        verdict_text = {
            "STRONG_ENTER": "Сильный сигнал на вход. Все агенты согласны, риски минимальны.",
            "ENTER": "Вход допустим. Большинство агентов дают положительный сигнал.",
            "WAIT": "Рекомендуется подождать. Есть факторы, требующие осторожности.",
            "AVOID": "Вход не рекомендуется. Высокий риск или плохое психологическое состояние.",
        }
        base = verdict_text.get(verdict, "Решение не определено.")
        if confidence < 50:
            base += f" Уверенность низкая ({confidence}%) из‑за расхождения мнений."
        return base

    def _enhance_with_llm(self, score: int, verdict: str, confidence: int,
                          market_score: int, risk_score: int, psychology_score: int) -> str:
        """Опциональное улучшение summary через LLM."""
        if not self.provider:
            return ""
        prompt = (
            f"Ты — главный трейдер-ментор. На основе консилиума дай краткий вердикт (1-2 предложения) "
            f"на русском языке, без воды.\n"
            f"Итоговый счёт: {score}/100\n"
            f"Вердикт: {verdict}\n"
            f"Уверенность: {confidence}%\n"
            f"Market: {market_score}/100, Risk: {risk_score}/100, Psychology: {psychology_score}/100\n\n"
            "Вердикт:"
        )
        try:
            return self.provider.generate(prompt).strip()
        except Exception:
            return ""