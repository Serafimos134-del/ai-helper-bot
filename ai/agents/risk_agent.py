import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder
from ai.risk_engine import RiskRuleEngine

logger = logging.getLogger(__name__)


class RiskAgent:
    """Агент, интерпретирующий сигналы риска от Rule Engine (полностью rule-based)."""

    def __init__(self, provider: BaseProvider = None):
        self.provider = provider  # больше не используется, оставлен для обратной совместимости
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        """Асинхронно возвращает JSON с сигналами риска + rule-based summary."""
        if context is None:
            ctx = await self.context_builder.build_full_context()
        else:
            ctx = context
        portfolio = ctx.get("portfolio", {})
        history = ctx.get("history", {})

        # Получаем сигналы от Rule Engine
        signals = RiskRuleEngine.assess(portfolio, history)

        # Генерируем summary из шаблона (без LLM)
        summary = self._build_template_summary(signals)

        result = {
            "risk_score": (10 - signals.get('risk_score', 5)) * 10,
            "signals": signals,
            "summary": summary
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    @staticmethod
    def _build_template_summary(signals: dict) -> str:
        """Генерирует summary на основе сигналов без LLM."""
        risk_level = signals.get('risk_level', 'UNKNOWN')
        risk_score = signals.get('risk_score', 0)
        warnings = signals.get('warnings', [])
        recommendation = signals.get('recommendation', '')

        level_text = {
            'SAFE': 'низкий',
            'MODERATE': 'умеренный',
            'HIGH': 'высокий',
            'EXTREME': 'критический',
        }
        level_str = level_text.get(risk_level, risk_level)

        main_warning = warnings[0] if warnings else 'критических проблем нет'

        rec_text = {
            'ALLOW': 'Можно продолжать текущую стратегию.',
            'REDUCE': 'Рекомендуется снизить размер позиций.',
            'CAUTION': 'Требуется осторожность при открытии новых позиций.',
            'STOP': 'Рекомендуется прекратить торговлю до стабилизации.',
        }
        rec_str = rec_text.get(recommendation, recommendation)

        return (
            f"Уровень риска: {risk_level} ({risk_score}/10) — {level_str}. "
            f"Главная проблема: {main_warning}. "
            f"Рекомендация: {rec_str}"
        )