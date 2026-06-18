import logging
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class PsychologyAgent:
    """Агент, анализирующий психологические паттерны трейдера (строгий стиль)."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    def analyze(self) -> str:
        """Анализирует историю сделок и возвращает оценку психологического состояния."""
        ctx = self.context_builder.build_full_context()
        history = ctx.get("history", {})
        prompt = self._build_psychology_prompt(history)
        return self.provider.generate(prompt)

    def _build_psychology_prompt(self, history: dict) -> str:
        stats = history.get("stats", {})
        recent_trades = history.get("recent_trades", [])
        losing_streak = history.get("losing_streak", 0)
        winning_streak = history.get("winning_streak", 0)

        trades_summary = ""
        for t in recent_trades[:10]:
            trades_summary += (
                f"{t.get('symbol')} {t.get('side')}: "
                f"PNL ${t.get('pnl', 0):+.2f}, "
                f"плечо {t.get('leverage', 1)}x, "
                f"комм.: {t.get('comment', '—')}\n"
            )

        return (
            "Ты — спортивный психолог, работающий с профессиональными трейдерами. "
            "Проанализируй историю сделок и дай КРАТКУЮ, ЖЁСТКУЮ оценку психологического состояния.\n\n"
            "ПРАВИЛА:\n"
            "1. СОСТОЯНИЕ: одно слово — CALM / NERVOUS / EMOTIONAL / REVENGE / TILT.\n"
            "2. ПАТТЕРН: одно предложение — главная психологическая ошибка (если есть).\n"
            "3. СОВЕТ: одно конкретное действие для исправления.\n"
            "4. Без воды, без философии, без markdown. Только факты.\n\n"
            f"Сделок всего: {stats.get('total_trades', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0):.1f}%\n"
            f"Серия убытков: {losing_streak}\n"
            f"Серия прибылей: {winning_streak}\n"
            f"Средняя прибыль: ${stats.get('avg_profit', 0):.2f}\n"
            f"Средний убыток: ${stats.get('avg_loss', 0):.2f}\n\n"
            f"Последние 10 сделок:\n{trades_summary}\n"
            "Твой вердикт:"
        )