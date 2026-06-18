import logging
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class MarketAgent:
    """Агент, анализирующий рыночную ситуацию."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    def analyze(self) -> str:
        """Анализирует рыночный контекст и возвращает заключение."""
        context = self.context_builder._build_market_context()
        prompt = self._build_market_prompt(context)
        return self.provider.generate(prompt)

    def _build_market_prompt(self, ctx: dict) -> str:
        btc = ctx.get("btc", {})
        eth = ctx.get("eth", {})
        top = ctx.get("top_movers", [])
        trend = ctx.get("trend", "NEUTRAL")

        prompt = (
            "Ты — профессиональный рыночный аналитик. Проанализируй текущую рыночную ситуацию "
            "на основе предоставленных данных и дай краткое заключение.\n\n"
            "ПРАВИЛА:\n"
            "1. ОБЩИЙ НАСТРОЙ: одно предложение — BULLISH / BEARISH / NEUTRAL.\n"
            "2. КЛЮЧЕВЫЕ УРОВНИ BTC и ETH: поддержка и сопротивление.\n"
            "3. ТОП-3 ДВИЖЕНИЯ: назови монеты с самым сильным ростом и падением.\n"
            "4. ПОТЕНЦИАЛЬНЫЕ ТОЧКИ ВХОДА: любые 2 монеты из списка с кратким обоснованием.\n"
            "Будь конкретен, используй цифры. Без философии.\n\n"
            "ДАННЫЕ:\n"
            f"Тренд: {trend}\n"
            f"BTC: цена ${btc.get('price', 0):.2f}, изменение за 24ч: {btc.get('change_24h', 0):+.2f}%, "
            f"макс: ${btc.get('high', 0):.2f}, мин: ${btc.get('low', 0):.2f}, объём: {btc.get('volume', 0):,.0f}\n"
            f"ETH: цена ${eth.get('price', 0):.2f}, изменение за 24ч: {eth.get('change_24h', 0):+.2f}%, "
            f"макс: ${eth.get('high', 0):.2f}, мин: ${eth.get('low', 0):.2f}, объём: {eth.get('volume', 0):,.0f}\n"
            f"Топ-5 по объёму: {top}\n\n"
            "Твой анализ:"
        )
        return prompt