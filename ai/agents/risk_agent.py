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

    def analyze(self) -> str:
        """Возвращает JSON с сигналами риска + текстовое summary."""
        ctx = self.context_builder.build_full_context()
        portfolio = ctx.get("portfolio", {})
        history = ctx.get("history", {})

        # 1. Получаем сигналы от Rule Engine
        signals = RiskRuleEngine.assess(portfolio, history)

        # 2. Генерируем человеческое summary через LLM
        try:
            summary_prompt = (
                f"Ты — строгий риск-менеджер. На основе сигналов риска дай краткий вывод (1-2 предложения) "
                f"на русском языке, без воды.\n"
                f"Уровень риска: {signals['risk_level']}\n"
                f"Счёт: {signals['risk_score']}/10\n"
                f"Предупреждения: {', '.join(signals['warnings']) if signals['warnings'] else 'нет'}\n"
                f"Рекомендация: {signals['recommendation']}\n\n"
                "Вывод:"
            )
            summary = self.provider.generate(summary_prompt).strip()
        except Exception as e:
            logger.error(f"Ошибка генерации summary: {e}")
            summary = "Риск-анализ завершён. Смотри детали."

        # 3. Возвращаем JSON + текстовый вывод
        result = {
            "signals": signals,
            "summary": summary
        }
        return json.dumps(result, ensure_ascii=False, indent=2)