import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder
from ai.risk_engine import RiskRuleEngine

logger = logging.getLogger(__name__)


class RiskAgent:
    """Агент, интерпретирующий сигналы риска от Rule Engine."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        """Асинхронно возвращает JSON с сигналами риска + текстовое summary."""
        loop = asyncio.get_running_loop()
        if context is None:
            ctx = await self.context_builder.build_full_context()   # ← исправлено: добавлен await
        else:
            ctx = context
        portfolio = ctx.get("portfolio", {})
        history = ctx.get("history", {})

        # Получаем сигналы от Rule Engine
        signals = RiskRuleEngine.assess(portfolio, history)

        # Генерируем human-readable summary через LLM с улучшенным промптом
        try:
            summary_prompt = self._build_summary_prompt(signals)
            summary = await loop.run_in_executor(None, self.provider.generate, summary_prompt)
            summary = summary.strip()
        except Exception as e:
            logger.error(f"Ошибка генерации summary: {e}")
            summary = "Риск-анализ завершён. Смотри детали."

        result = {
            "signals": signals,
            "summary": summary
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _build_summary_prompt(self, signals: dict) -> str:
        """Улучшенный промпт в стиле ponytail: строгий, конкретный, без воды."""
        risk_level = signals.get('risk_level', 'UNKNOWN')
        risk_score = signals.get('risk_score', 0)
        warnings = signals.get('warnings', [])
        recommendation = signals.get('recommendation', '')

        warnings_text = '\n'.join(f'- {w}' for w in warnings) if warnings else 'нет'

        return (
            "Ты — строгий риск-менеджер хедж-фонда. Твоя задача — дать краткий, "
            "максимально конкретный вывод о риск-профиле портфеля на русском языке.\n\n"
            "ПРАВИЛА:\n"
            "1. Одна фраза — общая оценка (SAFE/MODERATE/HIGH/EXTREME) и что она значит.\n"
            "2. Одна фраза — главная проблема (самый критичный warning). Если проблем нет, скажи, что всё в порядке.\n"
            "3. Одна фраза — конкретное действие (что делать прямо сейчас).\n"
            "4. Без воды, без markdown, без эмодзи, без общих фраз вроде «будьте осторожны».\n"
            "Используй цифры из сигналов ниже.\n\n"
            f"Уровень риска: {risk_level}\n"
            f"Счёт: {risk_score}/10\n"
            f"Рекомендация: {recommendation}\n"
            f"Предупреждения:\n{warnings_text}\n\n"
            "Вывод:"
        )