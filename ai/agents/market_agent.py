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
        # Защита от None: если ctx.get вернул None, заменяем на пустой словарь/список
        btc = ctx.get("btc") or {}
        eth = ctx.get("eth") or {}
        top = ctx.get("top_movers") or []
        trend = ctx.get("trend") or "NEUTRAL"

        # Безопасное получение числовых значений с защитой от None
        btc_price = btc.get('price', 0) or 0
        btc_change = btc.get('change_24h', 0) or 0
        btc_high = btc.get('high', 0) or 0
        btc_low = btc.get('low', 0) or 0
        btc_volume = btc.get('volume', 0) or 0

        eth_price = eth.get('price', 0) or 0
        eth_change = eth.get('change_24h', 0) or 0
        eth_high = eth.get('high', 0) or 0
        eth_low = eth.get('low', 0) or 0
        eth_volume = eth.get('volume', 0) or 0

        return (
            "Ты — профессиональный рыночный аналитик. Дай КРАТКИЙ, КОНКРЕТНЫЙ анализ текущей ситуации "
            "на основе предоставленных данных.\n\n"
            "ПРАВИЛА (жёсткие):\n"
            "1. ВЕРДИКТ: одно слово — BULLISH / BEARISH / NEUTRAL.\n"
            "2. КЛЮЧЕВЫЕ УРОВНИ: поддержка и сопротивление для BTC и ETH (конкретные цены).\n"
            "3. СИГНАЛ: BUY / SELL / WAIT с обоснованием в одно предложение.\n"
            "4. Без воды, без markdown, без общих фраз. Только цифры и факты.\n\n"
            f"Тренд: {trend}\n"
            f"BTC: цена ${btc_price:.2f}, изменение за 24ч: {btc_change:+.2f}%, "
            f"макс: ${btc_high:.2f}, мин: ${btc_low:.2f}, объём: {btc_volume:,.0f}\n"
            f"ETH: цена ${eth_price:.2f}, изменение за 24ч: {eth_change:+.2f}%, "
            f"макс: ${eth_high:.2f}, мин: ${eth_low:.2f}, объём: {eth_volume:,.0f}\n"
            f"Топ-5 по объёму: {top}\n\n"
            "Твой анализ:"
        )