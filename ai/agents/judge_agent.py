import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class JudgeAgent:
    """Финальный арбитр, использующий метрики из консенсус-движка и адаптирующий вердикт под режим анализа."""

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
        self.provider = provider

    async def synthesize(self, market_json: str, risk_json: str, psychology_json: str,
                         mode: str = None, trade_score: int = None,
                         confidence: float = None, disagreement: float = None) -> str:
        """
        Принимает JSON‑выводы агентов, метрики консенсуса и возвращает финальный вердикт.
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

        # Используем переданные из консенсуса confidence и disagreement, если они есть
        if confidence is None:
            scores = [market_score, risk_score, psychology_score]
            raw_disagreement = max(scores) - min(scores)
            confidence = max(20, 100 - raw_disagreement)
        if disagreement is None:
            scores = [market_score, risk_score, psychology_score]
            disagreement = max(scores) - min(scores)

        verdict = self._get_verdict(final_score, mode)

        warnings = []
        if risk_score < 40:
            warnings.append("Высокий риск")
        if psychology_score < 40:
            warnings.append("Психологическая нестабильность")
        if disagreement > 40:
            warnings.append("Сильное расхождение мнений агентов")

        summary = self._generate_summary(final_score, verdict, confidence, disagreement, mode)

        if self.provider:
            try:
                enhanced = await self._enhance_with_llm(final_score, verdict, confidence,
                                                        market_score, risk_score, psychology_score,
                                                        mode)
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
                "trade": final_trade_score,
            }
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    @classmethod
    def _get_verdict(cls, score: int, mode: str = None) -> str:
        # Для открытых позиций и закрытых сделок используем HOLD/CLOSE вместо ENTER/AVOID
        if mode in ('open', 'post_trade'):
            if score >= 70:
                return "HOLD" if mode == 'open' else "GOOD_TRADE"
            elif score >= 55:
                return "HOLD"  # для открытых позиций удержание, для закрытых - нейтрально
            else:
                return "CLOSE" if mode == 'open' else "BAD_TRADE"
        # Для сетапов и общего анализа используем классические вердикты
        for verdict, threshold in cls.THRESHOLDS.items():
            if score >= threshold:
                return verdict
        return "AVOID"

    @classmethod
    def _generate_summary(cls, score: int, verdict: str, confidence: float, disagreement: float,
                          mode: str = None) -> str:
        # Адаптивные сообщения в зависимости от режима
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

    async def _enhance_with_llm(self, score: int, verdict: str, confidence: float,
                                market_score: int, risk_score: int, psychology_score: int,
                                mode: str = None) -> str:
        if not self.provider:
            return ""
        mode_context = {
            'open': 'открытая позиция',
            'post_trade': 'закрытая сделка',
            'setup': 'новый сетап'
        }.get(mode, 'анализ')

        prompt = (
            f"Ты — главный трейдер-ментор. На основе консилиума дай краткий вердикт (1-2 предложения) "
            f"на русском языке для {mode_context}.\n"
            f"Итоговый счёт: {score}/100\n"
            f"Вердикт: {verdict}\n"
            f"Уверенность: {confidence:.0%}\n"
            f"Market: {market_score}/100, Risk: {risk_score}/100, Psychology: {psychology_score}/100\n\n"
            "Твой вердикт (не повторяй цифры, только суть):"
        )
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self.provider.generate, prompt)
            return result.strip()
        except Exception:
            return ""