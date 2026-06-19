import asyncio
import logging
from ai.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class JudgeAgent:
    """Финальный арбитр с матрицей решений v2 (строгий стиль)."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider

    async def synthesize(self, market_analysis: str, risk_analysis: str, psychology_analysis: str, mode: str = 'setup') -> str:
        loop = asyncio.get_running_loop()
        prompt = self._build_judge_prompt(market_analysis, risk_analysis, psychology_analysis, mode)
        try:
            return await loop.run_in_executor(None, self.provider.generate, prompt)
        except Exception as e:
            logger.error(f"JudgeAgent error: {e}")
            return f"Финальный вердикт недоступен: {e}"

    def _build_judge_prompt(self, market: str, risk: str, psychology: str, mode: str = 'setup') -> str:
        if mode == 'open':
            action_line = (
                "1. ВЕРДИКТ (выбери ОДИН): STRONG_HOLD / HOLD / REDUCE / CLOSE / ADD.\n"
                "   - STRONG_HOLD: уверенно держать, всё отлично.\n"
                "   - HOLD: держать, но следить.\n"
                "   - REDUCE: сократить позицию.\n"
                "   - CLOSE: закрыть полностью.\n"
                "   - ADD: можно добавить к позиции.\n"
            )
        elif mode == 'post_trade':
            action_line = (
                "1. ВЕРДИКТ (выбери ОДИН): EXCELLENT / GOOD / ACCEPTABLE / BAD / TERRIBLE.\n"
                "   - EXCELLENT: идеальная сделка.\n"
                "   - GOOD: хорошая сделка, мелкие недочёты.\n"
                "   - ACCEPTABLE: нормально, но можно лучше.\n"
                "   - BAD: плохая сделка, ошибки.\n"
                "   - TERRIBLE: грубая ошибка, нарушение правил.\n"
            )
        else:
            action_line = (
                "1. ВЕРДИКТ (выбери ОДИН): STRONG_ENTER / ENTER / CAUTIOUS_ENTER / WAIT / AVOID.\n"
                "   - STRONG_ENTER: отличный сетап, уверенный вход.\n"
                "   - ENTER: хороший сетап, можно входить.\n"
                "   - CAUTIOUS_ENTER: входить осторожно, уменьшенным размером.\n"
                "   - WAIT: пока не входить, ждать лучших условий.\n"
                "   - AVOID: категорически не входить.\n"
            )

        return (
            "Ты — главный трейдер-ментор хедж-фонда. Три эксперта дали заключения. "
            "Твоя задача — принять ОКОНЧАТЕЛЬНОЕ РЕШЕНИЕ на основе их мнений.\n\n"
            "ПРАВИЛА (жёсткие):\n"
            f"{action_line}"
            "2. ОБОСНОВАНИЕ: одно предложение — почему именно так.\n"
            "3. КОНФЛИКТ: если агенты противоречат друг другу, укажи это и объясни, чьё мнение перевесило.\n"
            "4. РИСК-ПРИОРИТЕТ: если RiskAgent говорит HIGH или EXTREME, вердикт НЕ может быть STRONG_ENTER или ENTER.\n"
            "5. ПСИХОЛОГИЯ-ПРИОРИТЕТ: если PsychologyAgent говорит REVENGE или TILT, вердикт НЕ может быть STRONG_ENTER.\n"
            "6. Будь скептичным. Если данные противоречивы или слабы — выбирай более осторожный вердикт.\n"
            "7. Без воды, без markdown, без общих фраз. Только конкретный вердикт и чёткое обоснование.\n\n"
            f"МНЕНИЕ MARKET AGENT:\n{market}\n\n"
            f"МНЕНИЕ RISK AGENT:\n{risk}\n\n"
            f"МНЕНИЕ PSYCHOLOGY AGENT:\n{psychology}\n\n"
            "ИТОГОВОЕ РЕШЕНИЕ:"
        )