import logging
from ai.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class JudgeAgent:
    """Финальный арбитр, синтезирующий выводы всех агентов."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider

    def synthesize(self, market_analysis: str, risk_analysis: str, psychology_analysis: str) -> str:
        """Принимает выводы агентов и возвращает финальное решение."""
        prompt = self._build_judge_prompt(market_analysis, risk_analysis, psychology_analysis)
        return self.provider.generate(prompt)

    def _build_judge_prompt(self, market: str, risk: str, psychology: str) -> str:
        prompt = (
            "Ты — главный трейдер-ментор. Три эксперта дали свои заключения по текущей ситуации. "
            "Твоя задача — синтезировать их выводы и дать итоговую рекомендацию.\n\n"
            "ПРАВИЛА:\n"
            "1. УЧТИ ВСЕ ТРИ МНЕНИЯ. Если они противоречат друг другу, объясни почему.\n"
            "2. ИТОГОВЫЙ ВЕРДИКТ: одно слово — ВХОДИТЬ / ЖДАТЬ / НЕ ВХОДИТЬ.\n"
            "3. ОБОСНОВАНИЕ: 2-3 предложения, ссылаясь на конкретные выводы агентов.\n"
            "4. РИСК-ПРЕДУПРЕЖДЕНИЕ: если риск высокий или психология нестабильна, "
            "рекомендация должна быть осторожной.\n"
            "Будь строг и конкретен.\n\n"
            f"МНЕНИЕ MARKET AGENT:\n{market}\n\n"
            f"МНЕНИЕ RISK AGENT:\n{risk}\n\n"
            f"МНЕНИЕ PSYCHOLOGY AGENT:\n{psychology}\n\n"
            "ИТОГОВОЕ РЕШЕНИЕ:"
        )
        return prompt