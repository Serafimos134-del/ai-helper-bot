import asyncio
import logging
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class PsychologyAgent:
    """Агент, анализирующий психологические паттерны трейдера (строгий стиль)."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        """Асинхронно анализирует историю сделок и возвращает оценку психологического состояния."""
        loop = asyncio.get_running_loop()
        if context is None:
            ctx = self.context_builder.build_full_context()
        else:
            ctx = context
        history = ctx.get("history", {})
        prompt = self._build_psychology_prompt(ctx)
        try:
            return await loop.run_in_executor(None, self.provider.generate, prompt)
        except Exception as e:
            logger.error(f"PsychologyAgent error: {e}")
            return f"Психологический анализ недоступен: {e}"

    def _build_psychology_prompt(self, ctx: dict) -> str:
        history = ctx.get("history", {})
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

        # Извлекаем информацию о текущей ситуации
        idea = ctx.get("idea", {})
        position = ctx.get("position", {})
        ticker = idea.get("ticker") or position.get("symbol", "")
        direction = idea.get("direction") or position.get("side", "")

        situation = ""
        if ticker:
            situation = f"Трейдер рассматривает вход в {direction} по {ticker}.\n"
        elif position:
            situation = f"Трейдер удерживает позицию {direction} по {ticker}.\n"

        prompt = (
            "Ты — спортивный психолог, работающий с профессиональными трейдерами. "
            "Проанализируй историю сделок и дай КРАТКУЮ, ЖЁСТКУЮ оценку психологического состояния "
            "на основе предоставленных данных.\n\n"
            "ПРАВИЛА:\n"
            "1. СОСТОЯНИЕ: одно слово — CALM / NERVOUS / EMOTIONAL / REVENGE / TILT.\n"
            "2. ПАТТЕРН: одно предложение — главная психологическая ошибка (если есть).\n"
            "3. СОВЕТ: одно конкретное действие для исправления (если нужно) или подтверждение правильного настроя.\n"
            "4. Если история пуста (0 сделок), честно напиши: «Нет данных для анализа», но не выдумывай.\n"
            "5. Без воды, без философии, без markdown. Только факты.\n\n"
            f"{situation}"
            f"Сделок всего: {stats.get('total_trades', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0):.1f}%\n"
            f"Серия убытков: {losing_streak}\n"
            f"Серия прибылей: {winning_streak}\n"
            f"Средняя прибыль: ${stats.get('avg_profit', 0):.2f}\n"
            f"Средний убыток: ${stats.get('avg_loss', 0):.2f}\n\n"
            f"Последние 10 сделок:\n{trades_summary}\n"
            "Твой вердикт:"
        )
        return prompt