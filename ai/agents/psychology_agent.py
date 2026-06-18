import logging
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class PsychologyAgent:
    """Агент, анализирующий психологические паттерны трейдера."""

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
        for t in recent_trades[:15]:
            trades_summary += (
                f"{t.get('symbol')} {t.get('side')}: "
                f"PNL ${t.get('pnl', 0):+.2f}, "
                f"плечо {t.get('leverage', 1)}x, "
                f"комментарий: {t.get('comment', 'нет')}\n"
            )

        prompt = (
            "Ты — спортивный психолог, специализирующийся на трейдинге. "
            "Проанализируй историю сделок трейдера и выяви психологические паттерны.\n\n"
            "ПРАВИЛА:\n"
            "1. ОЦЕНИ СОСТОЯНИЕ: CALM / NERVOUS / EMOTIONAL / REVENGE / TILT.\n"
            "2. НАЙДИ ПАТТЕРН: повторяющиеся ошибки, импульсивные входы, нарушение риск-менеджмента.\n"
            "3. ДАЙ СОВЕТ: одно конкретное действие для улучшения психологии.\n"
            "Будь конкретен, ссылайся на данные из истории.\n\n"
            "ДАННЫЕ:\n"
            f"Всего сделок: {stats.get('total_trades', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0):.1f}%\n"
            f"Текущая серия убытков: {losing_streak}\n"
            f"Текущая серия прибылей: {winning_streak}\n"
            f"Средняя прибыль: ${stats.get('avg_profit', 0):.2f}\n"
            f"Средний убыток: ${stats.get('avg_loss', 0):.2f}\n"
            f"Лучшая сделка: ${stats.get('best_trade', 0):.2f}\n"
            f"Худшая сделка: ${stats.get('worst_trade', 0):.2f}\n\n"
            f"Последние сделки:\n{trades_summary}\n"
            "Твой анализ:"
        )
        return prompt