import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class MarketAgent:
    """Агент, анализирующий рыночную ситуацию."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        loop = asyncio.get_running_loop()
        if context is None:
            context = self.context_builder._build_market_context()
        prompt = self._build_market_prompt(context)
        logger.info(f"MARKET AGENT PROMPT:\n{prompt}")
        try:
            response = await loop.run_in_executor(None, self.provider.generate, prompt)

            if response.startswith("AI analysis unavailable"):
                return json.dumps({"market_score": 0, "analysis": response}, ensure_ascii=False)

            try:
                start = response.find('{')
                end   = response.rfind('}') + 1
                if start >= 0 and end > start:
                    parsed = json.loads(response[start:end])
                    if 'market_score' not in parsed:
                        parsed['market_score'] = 50
                    if 'analysis' not in parsed:
                        parsed['analysis'] = response
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass

            return json.dumps({"market_score": 50, "analysis": response}, ensure_ascii=False)

        except Exception as e:
            logger.error(f"MarketAgent error: {e}")
            return json.dumps({"market_score": 0, "analysis": f"Рыночный анализ недоступен: {e}"}, ensure_ascii=False)

    def _build_market_prompt(self, ctx: dict) -> str:
        market       = ctx.get("market", {}) or ctx
        btc          = market.get("btc") or {}
        eth          = market.get("eth") or {}
        top          = market.get("top_movers") or []
        trend        = market.get("trend") or "NEUTRAL"
        market_regime = market.get("market_regime", "UNKNOWN")
        ticker_info  = ctx.get("ticker") or {}
        idea         = ctx.get("idea") or {}

        btc_price  = btc.get('price', 0) or 0
        btc_change = btc.get('change_24h', 0) or 0
        btc_high   = btc.get('high', 0) or 0
        btc_low    = btc.get('low', 0) or 0
        eth_price  = eth.get('price', 0) or 0
        eth_change = eth.get('change_24h', 0) or 0

        if ticker_info:
            symbol    = idea.get("symbol", ticker_info.get("symbol", ""))
            direction = idea.get("direction", "")
            price     = ticker_info.get('price', 'N/A')
            change    = ticker_info.get('change_24h', 0)
            high      = ticker_info.get('high', 'N/A')
            low       = ticker_info.get('low', 'N/A')
            volume    = ticker_info.get('volume', 0)
            funding   = ticker_info.get('funding_rate', 'N/A')
            oi        = ticker_info.get('open_interest', 'N/A')
            atr       = ticker_info.get('atr', 'N/A')
            regime    = ticker_info.get('market_regime', 'UNKNOWN')

            prompt = f"""Ты — опытный трейдер-аналитик криптофьючерсов. Проанализируй рыночную ситуацию и дай чёткое мнение.

ДАННЫЕ ПО {symbol}:
- Цена: {price} | Изменение 24ч: {change:+.2f}%
- Максимум: {high} | Минимум: {low}
- Объём 24ч: {volume:,.0f} USDT
- Funding Rate: {funding} (положительный = перегрев лонгов)
- Open Interest: {oi}
- ATR(14): {atr} — волатильность
- Режим рынка: {regime}

ОБЩИЙ КОНТЕКСТ:
- BTC: ${btc_price:.2f} ({btc_change:+.2f}%) | Режим: {market_regime}
- ETH: ${eth_price:.2f} ({eth_change:+.2f}%)
- Общий тренд: {trend}

ЗАДАЧА: Оцени ситуацию для {'направления ' + direction if direction else 'новой позиции'} по {symbol}.

Напиши анализ в 2-3 предложениях на русском языке. Конкретно:
1. Что сейчас происходит с ценой и объёмом
2. Есть ли признаки разворота или продолжения
3. Твой вывод: входить сейчас или нет и почему

Без перечисления цифр из данных выше — только твои выводы и интерпретация.

Верни строго JSON (без markdown, без ```):
{{"market_score": <число 0-100>, "analysis": "<твой анализ 2-3 предложения>"}}

market_score: 85-100=сильный рост, 70-84=умеренный рост, 55-69=нейтрально, 40-54=умеренное снижение, <40=сильное снижение"""

        else:
            prompt = f"""Ты — опытный трейдер-аналитик криптофьючерсов. Дай краткий обзор рынка.

ДАННЫЕ:
- BTC: ${btc_price:.2f} ({btc_change:+.2f}%) | Макс: ${btc_high:.2f} | Мин: ${btc_low:.2f}
- ETH: ${eth_price:.2f} ({eth_change:+.2f}%)
- Режим рынка: {market_regime} | Тренд: {trend}
- Топ монеты по объёму: {top}

Напиши анализ в 2-3 предложениях на русском:
1. Общий настрой рынка прямо сейчас
2. На что обратить внимание трейдеру
3. Рекомендация: осторожно / активно / лучше подождать

Без перечисления цифр — только твои выводы.

Верни строго JSON (без markdown, без ```):
{{"market_score": <число 0-100>, "analysis": "<твой анализ 2-3 предложения>"}}"""

        return prompt
