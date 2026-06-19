import asyncio
import logging
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class MarketAgent:
    """Агент, анализирующий рыночную ситуацию в стиле ponytail."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        """Асинхронно анализирует рыночный контекст и возвращает структурированное заключение."""
        loop = asyncio.get_running_loop()
        if context is None:
            context = self.context_builder._build_market_context()
        prompt = self._build_market_prompt(context)
        logger.info(f"MARKET AGENT PROMPT:\n{prompt}")
        try:
            return await loop.run_in_executor(None, self.provider.generate, prompt)
        except Exception as e:
            logger.error(f"MarketAgent error: {e}")
            return f"Рыночный анализ недоступен: {e}"

    def _build_market_prompt(self, ctx: dict) -> str:
        # Общий рыночный контекст
        market = ctx.get("market", {}) or ctx
        btc = market.get("btc") or {}
        eth = market.get("eth") or {}
        top = market.get("top_movers") or []
        trend = market.get("trend") or "NEUTRAL"

        # Данные по конкретному инструменту (если есть)
        ticker_info = ctx.get("ticker") or {}
        idea = ctx.get("idea") or {}

        btc_price = btc.get('price', 0) or 0
        btc_change = btc.get('change_24h', 0) or 0
        btc_high = btc.get('high', 0) or 0
        btc_low = btc.get('low', 0) or 0

        eth_price = eth.get('price', 0) or 0
        eth_change = eth.get('change_24h', 0) or 0
        eth_high = eth.get('high', 0) or 0
        eth_low = eth.get('low', 0) or 0

        # Строим промпт
        prompt = (
            "Ты — профессиональный рыночный аналитик. Дай КРАТКИЙ, КОНКРЕТНЫЙ анализ текущей ситуации "
            "на основе предоставленных данных.\n\n"
            "ПРАВИЛА (жёсткие):\n"
            "1. ВЕРДИКТ: одно слово — BULLISH / BEARISH / NEUTRAL.\n"
        )

        # Если есть конкретный инструмент – добавим анализ по нему
        if ticker_info:
            symbol = idea.get("symbol", ticker_info.get("symbol", ""))
            direction = idea.get("direction", "")
            prompt += (
                f"2. АНАЛИЗ {symbol}: текущая цена {ticker_info.get('price', 'N/A')}, "
                f"изменение за 24ч: {ticker_info.get('change_24h', 0):+.2f}%, "
                f"макс: {ticker_info.get('high', 'N/A')}, мин: {ticker_info.get('low', 'N/A')}.\n"
                f"3. СИГНАЛ ДЛЯ {direction if direction else 'сделки'}: BUY / SELL / WAIT с обоснованием в одно предложение.\n"
            )
        else:
            prompt += (
                "2. КЛЮЧЕВЫЕ УРОВНИ: поддержка и сопротивление для BTC и ETH (конкретные цены).\n"
                "3. СИГНАЛ: BUY / SELL / WAIT с обоснованием в одно предложение.\n"
            )

        prompt += (
            "4. Без воды, без markdown, без общих фраз. Только цифры и факты.\n\n"
            f"Тренд: {trend}\n"
            f"BTC: цена ${btc_price:.2f}, изменение за 24ч: {btc_change:+.2f}%, "
            f"макс: ${btc_high:.2f}, мин: ${btc_low:.2f}\n"
            f"ETH: цена ${eth_price:.2f}, изменение за 24ч: {eth_change:+.2f}%, "
            f"макс: ${eth_high:.2f}, мин: ${eth_low:.2f}\n"
            f"Топ-5 по объёму: {top}\n\n"
            "Твой анализ:"
        )
        return prompt