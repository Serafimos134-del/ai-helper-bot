import logging
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class MarketAgent:
    """Агент, анализирующий рыночную ситуацию в стиле ponytail."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    def analyze(self) -> str:
        """Анализирует рыночный контекст и возвращает структурированное заключение."""
        context = self.context_builder._build_market_context()
        prompt = self._build_market_prompt(context)
        return self.provider.generate(prompt)

    def _build_market_prompt(self, ctx: dict) -> str:
        btc = ctx.get("btc", {})
        eth = ctx.get("eth", {})
        top = ctx.get("top_movers", [])
        trend = ctx.get("trend", "NEUTRAL")

        return (
            "Ты — профессиональный рыночный аналитик. Дай КРАТКИЙ, КОНКРЕТНЫЙ анализ текущей ситуации "
            "на основе предоставленных данных.\n\n"
            "ПРАВИЛА (жёсткие):\n"
            "1. ВЕРДИКТ: одно слово — BULLISH / BEARISH / NEUTRAL.\n"
            "2. КЛЮЧЕВЫЕ УРОВНИ: поддержка и сопротивление для BTC и ETH (конкретные цены).\n"
            "3. СИГНАЛ: BUY / SELL / WAIT с обоснованием в одно предложение.\n"
            "4. Без воды, без markdown, без общих фраз. Только цифры и факты.\n\n"
            f"Тренд: {trend}\n"
            f"BTC: цена ${btc.get('price', 0):.2f}, изменение за 24ч: {btc.get('change_24h', 0):+.2f}%, "
            f"макс: ${btc.get('high', 0):.2f}, мин: ${btc.get('low', 0):.2f}, объём: {btc.get('volume', 0):,.0f}\n"
            f"ETH: цена ${eth.get('price', 0):.2f}, изменение за 24ч: {eth.get('change_24h', 0):+.2f}%, "
            f"макс: ${eth.get('high', 0):.2f}, мин: ${eth.get('low', 0):.2f}, объём: {eth.get('volume', 0):,.0f}\n"
            f"Топ-5 по объёму: {top}\n\n"
            "Твой анализ:"
        )