import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)

class MarketAgent:
    """Агент, анализирующий рыночную ситуацию, включая forensic‑разбор сделок в post_trade."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        loop = asyncio.get_running_loop()
        if context is None:
            context = {}
        prompt = self._build_prompt(context)
        logger.info(f"MARKET AGENT PROMPT:\n{prompt}")
        try:
            response = await loop.run_in_executor(None, self.provider.generate, prompt)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"MarketAgent error: {e}")
            return json.dumps({"market_score": 0, "analysis": f"Рыночный анализ недоступен: {e}"}, ensure_ascii=False)

    def _parse_response(self, response: str) -> str:
        if response.startswith("AI analysis unavailable"):
            return json.dumps({"market_score": 0, "analysis": response}, ensure_ascii=False)
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
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

    def _build_prompt(self, ctx: dict) -> str:
        mode = ctx.get('mode', 'open')
        trade = ctx.get('trade')
        position = ctx.get('position')
        ticker_info = ctx.get('ticker') or {}
        idea = ctx.get('idea') or {}
        market = ctx.get('market', {}) or {}
        btc = market.get('btc') or {}
        eth = market.get('eth') or {}
        trend = market.get('trend', 'NEUTRAL')
        regime = market.get('market_regime', 'UNKNOWN')

        if mode == 'post_trade' and trade:
            return self._build_post_trade_prompt(trade, market)
        elif position:
            return self._build_open_position_prompt(position, ticker_info, market)
        elif idea:
            return self._build_setup_prompt(idea, ticker_info, market)
        else:
            # общий обзор рынка
            return self._build_general_market_prompt(market)

    def _build_post_trade_prompt(self, trade: dict, market: dict) -> str:
        symbol = trade.get('symbol', '')
        entry = trade.get('entry_price', 0)
        exit_p = trade.get('exit_price', 0)
        pnl = trade.get('realized_pnl', 0)
        sl = trade.get('stop_loss')
        tp = trade.get('take_profit')
        duration = trade.get('holding_minutes', '?')
        market_trend = trade.get('market_trend') or 'UNKNOWN'
        btc = market.get('btc') or {}
        regime = market.get('market_regime', 'UNKNOWN')

        prompt = f"""You are a forensic trade analyst. Analyze ONLY the provided closed trade.

TRADE OBJECT:
Symbol: {symbol}
Side: {trade.get('side', '')}
Entry: {entry}, Exit: {exit_p}, PnL: {pnl}
Stop Loss: {sl}, Take Profit: {tp}
Duration: {duration} min
Market trend at close: {market_trend}
Overall market regime: {regime}, BTC: {btc.get('price', '?')}

RULES:
- Do NOT describe general market trends unless directly impacting this trade outcome.
- Focus on: market state at entry, market state at exit, whether the trade was aligned with structure.
- Infer only from trade evidence, not external assumptions.

Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"market_score": <0-100>, "analysis": "<your concise analysis>"}}
"""
        return prompt

    def _build_open_position_prompt(self, pos: dict, ticker: dict, market: dict) -> str:
        symbol = pos.get('symbol', '')
        side = pos.get('side', '')
        entry = pos.get('entry_price', 0)
        pnl = pos.get('unrealized_pnl', 0)
        sl = pos.get('stop_loss')
        tp = pos.get('take_profit')
        btc = market.get('btc') or {}
        regime = market.get('market_regime', 'UNKNOWN')

        prompt = f"""You are a market analyst evaluating an OPEN position.

POSITION:
Symbol: {symbol} {side}
Entry: {entry}, Unrealized PnL: {pnl}
Stop Loss: {sl}, Take Profit: {tp}
Market regime: {regime}, BTC: {btc.get('price', '?')}

Evaluate whether current market conditions support holding this position.
Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"market_score": <0-100>, "analysis": "<your evaluation>"}}
"""
        return prompt

    def _build_setup_prompt(self, idea: dict, ticker: dict, market: dict) -> str:
        symbol = idea.get('symbol', '')
        direction = idea.get('direction', '')
        price = ticker.get('price', '?')
        change = ticker.get('change_24h', 0)
        funding = ticker.get('funding_rate', '?')
        atr = ticker.get('atr', '?')
        regime = ticker.get('market_regime', 'UNKNOWN')
        btc = market.get('btc') or {}

        prompt = f"""You are a market analyst evaluating a NEW trade setup.

SETUP: {symbol} {direction}
Current price: {price}, 24h change: {change}%, Funding: {funding}, ATR: {atr}, Regime: {regime}
BTC: {btc.get('price', '?')}

Is the market environment favorable for this setup?
Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"market_score": <0-100>, "analysis": "<your evaluation>"}}
"""
        return prompt

    def _build_general_market_prompt(self, market: dict) -> str:
        btc = market.get('btc') or {}
        eth = market.get('eth') or {}
        trend = market.get('trend', 'NEUTRAL')
        regime = market.get('market_regime', 'UNKNOWN')
        prompt = f"""Give a brief market overview in Russian (2-3 sentences).
BTC: {btc.get('price', '?')}, ETH: {eth.get('price', '?')}, Trend: {trend}, Regime: {regime}.
Return ONLY valid JSON: {{"market_score": <0-100>, "analysis": "<your overview>"}}"""
        return prompt