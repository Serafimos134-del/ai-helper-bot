import asyncio
import logging
from ai.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class JudgeAgent:
    """Финальный арбитр с элементами дебатов (строгий стиль)."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider

    async def synthesize(self, market_analysis: str, risk_analysis: str, psychology_analysis: str, mode: str = 'setup') -> str:
        """Асинхронно принимает выводы агентов и возвращает финальное решение."""
        loop = asyncio.get_running_loop()
        prompt = self._build_judge_prompt(market_analysis, risk_analysis, psychology_analysis, mode)
        try:
            return await loop.run_in_executor(None, self.provider.generate, prompt)
        except Exception as e:
            logger.error(f"JudgeAgent error: {e}")
            return f"Финальный вердикт недоступен: {e}"

    def _build_judge_prompt(self, market: str, risk: str, psychology: str, mode: str = 'setup') -> str:
        if mode == 'open':
            action_line = "1. ВЕРДИКТ: одно слово — ДЕРЖАТЬ / СОКРАТИТЬ / ЗАКРЫТЬ / ДОБАВИТЬ.\n"
        elif mode == 'post_trade':
            action_line = "1. ВЕРДИКТ: одно слово — ОТЛИЧНО / ХОРОШО / ПЛОХО.\n"
        else:
            action_line = "1. ВЕРДИКТ: одно слово — ВХОДИТЬ / ЖДАТЬ / НЕ ВХОДИТЬ.\n"

        return (
            "Ты — главный трейдер-ментор. Три эксперта дали заключения. "
            "Твоя задача — принять ОКОНЧАТЕЛЬНОЕ РЕШЕНИЕ на основе их мнений.\n\n"
            "ПРАВИЛА (жёсткие):\n"
            f"{action_line}"
            "2. ОБОСНОВАНИЕ: одно предложение — почему именно так.\n"
            "3. КОНФЛИКТ: если агенты противоречат друг другу, укажи это и объясни, чьё мнение перевесило.\n"
            "4. РИСК-ПРЕДУПРЕЖДЕНИЕ: если риск HIGH или EXTREME, вердикт должен быть ЖДАТЬ или НЕ ВХОДИТЬ.\n"
            "5. Без воды, без markdown, без общих фраз.\n\n"
            f"МНЕНИЕ MARKET AGENT:\n{market}\n\n"
            f"МНЕНИЕ RISK AGENT:\n{risk}\n\n"
            f"МНЕНИЕ PSYCHOLOGY AGENT:\n{psychology}\n\n"
            "ИТОГОВОЕ РЕШЕНИЕ:"
        )