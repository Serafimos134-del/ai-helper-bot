import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class MarketAgent:
    """Агент, анализирующий рыночную ситуацию (расширенный контекст v2)."""

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
            response = await loop.run_in_executor(None, self.provider.generate, prompt)
            # Извлекаем JSON из ответа LLM
            try:
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    parsed = json.loads(response[start:end])
                    # Убеждаемся что есть market_score
                    if 'market_score' not in parsed:
                        parsed['market_score'] = 50
                    if 'analysis' not in parsed:
                        parsed['analysis'] = response
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
            # Fallback
            fallback = {"market_score": 50, "analysis": response}
            return json.dumps(fallback, ensure_ascii=False)
        except Exception as e:
            logger.error(f"MarketAgent error: {e}")
            return json.dumps({"market_score": 0, "analysis": f"Рыночный анализ недоступен: {e}"}, ensure_ascii=False)

    def _build_market_prompt(self, ctx: dict) -> str:
        # Общий рыночный контекст
        market = ctx.get("market", {}) or ctx
        btc = market.get("btc") or {}
        eth = market.get("eth") or {}
        top = market.get("top_movers") or []
        trend = market.get("trend") or "NEUTRAL"
        market_regime = market.get("market_regime", "UNKNOWN")

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
            price = ticker_info.get('price', 'N/A')
            change = ticker_info.get('change_24h', 0)
            high = ticker_info.get('high', 'N/A')
            low = ticker_info.get('low', 'N/A')
            volume = ticker_info.get('volume', 0)
            funding = ticker_info.get('funding_rate', 'N/A')
            oi = ticker_info.get('open_interest', 'N/A')
            atr = ticker_info.get('atr', 'N/A')
            ticker_regime = ticker_info.get('market_regime', 'UNKNOWN')

            prompt += (
                f"2. АНАЛИЗ {symbol}:\n"
                f"   - Цена: {price}, изменение 24ч: {change:+.2f}%\n"
                f"   - Макс 24ч: {high}, Мин 24ч: {low}\n"
                f"   - Объём 24ч: {volume:,.0f}\n"
                f"   - Funding Rate: {funding}\n"
                f"   - Open Interest: {oi}\n"
                f"   - ATR (14): {atr} (волатильность)\n"
                f"   - Режим рынка: {ticker_regime}\n"
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
            f"Режим рынка BTC: {market_regime}\n"
            f"BTC: цена ${btc_price:.2f}, изм 24ч: {btc_change:+.2f}%, "
            f"макс: ${btc_high:.2f}, мин: ${btc_low:.2f}\n"
            f"ETH: цена ${eth_price:.2f}, изм 24ч: {eth_change:+.2f}%, "
            f"макс: ${eth_high:.2f}, мин: ${eth_low:.2f}\n"
            f"Топ-5 по объёму: {top}\n\n"
            "Верни ответ строго в формате JSON:\n"
            '{"market_score": <число 0-100>, "analysis": "<твой текстовый анализ>"}\n\n'
            "market_score — твоя оценка уверенности в направлении:\n"
            "85-100: сильный BUY-сигнал\n"
            "70-84: умеренный BUY\n"
            "55-69: нейтрально / WAIT\n"
            "40-54: слабый SELL\n"
            "менее 40: сильный SELL\n"
        )
        return prompt