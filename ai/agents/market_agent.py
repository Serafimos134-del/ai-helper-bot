import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class MarketAgent:
    """Агент рыночного анализа. Явный routing по mode — никаких fallback без предупреждения."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        loop = asyncio.get_running_loop()
        if context is None:
            context = {}
        prompt = self._build_prompt(context)
        logger.info(f"MARKET AGENT PROMPT (mode={context.get('mode', '?')}):\n{prompt}")
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

    def _build_prompt(self, ctx: dict) -> str:
        """Явный routing по mode — не угадываем по наличию полей."""
        mode     = ctx.get('mode', 'open')
        market   = ctx.get('market', {}) or {}
        ticker   = ctx.get('ticker') or {}
        idea     = ctx.get('idea') or {}
        position = ctx.get('position') or {}
        trade    = ctx.get('trade') or {}

        if mode == 'post_trade':
            if trade:
                return self._build_post_trade_prompt(trade, market)
            logger.warning("MarketAgent: mode=post_trade but no trade in context")
            return self._build_general_market_prompt(market)

        if mode == 'open':
            if position:
                return self._build_open_position_prompt(position, ticker, market)
            logger.warning("MarketAgent: mode=open but no position in context")
            return self._build_general_market_prompt(market)

        if mode == 'setup':
            if idea:
                return self._build_setup_prompt(idea, ticker, market)
            logger.warning("MarketAgent: mode=setup but no idea in context")
            return self._build_general_market_prompt(market)

        # Неизвестный mode — общий обзор с предупреждением
        logger.warning(f"MarketAgent: unknown mode={mode}, falling back to general market")
        return self._build_general_market_prompt(market)

    def _build_post_trade_prompt(self, trade: dict, market: dict) -> str:
        symbol   = trade.get('symbol', '')
        entry    = trade.get('entry_price', 0)
        exit_p   = trade.get('exit_price', 0)
        pnl      = trade.get('realized_pnl', 0)
        sl       = trade.get('stop_loss')
        tp       = trade.get('take_profit')
        sl_str   = f"${float(sl):.4f}" if sl is not None else "не установлен"
        tp_str   = f"${float(tp):.4f}" if tp is not None else "не установлен"
        duration = trade.get('holding_minutes', '?')
        btc      = market.get('btc') or {}
        regime   = market.get('market_regime', 'UNKNOWN')

        return f"""You are a forensic trade analyst. Analyze ONLY this closed trade.

TRADE:
Symbol: {symbol} {trade.get('side', '')}
Entry: {entry}, Exit: {exit_p}, PnL: {pnl}
Stop Loss: {sl_str}, Take Profit: {tp_str}
Duration: {duration} min
Market regime: {regime}, BTC: {btc.get('price', '?')}

RULES:
- Focus on: market state at entry vs exit, whether trade aligned with structure.
- Do NOT give generic market advice. Only evaluate this specific trade.
- If SL/TP are not set, note this explicitly.

Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"market_score": <0-100>, "analysis": "<your concise analysis>"}}
"""

    def _build_open_position_prompt(self, pos: dict, ticker: dict, market: dict) -> str:
        symbol = pos.get('symbol', '')
        side   = pos.get('side', '')
        entry  = pos.get('entry_price', 0)
        pnl    = pos.get('unrealized_pnl', 0)
        sl     = pos.get('stop_loss')
        tp     = pos.get('take_profit')
        sl_str = f"${float(sl):.4f}" if sl is not None else "не установлен"
        tp_str = f"${float(tp):.4f}" if tp is not None else "не установлен"
        btc    = market.get('btc') or {}
        regime = market.get('market_regime', 'UNKNOWN')

        return f"""You are a market analyst evaluating an OPEN position.

POSITION:
Symbol: {symbol} {side}
Entry: {entry}, Unrealized PnL: {pnl}
Stop Loss: {sl_str}, Take Profit: {tp_str}
Market regime: {regime}, BTC: {btc.get('price', '?')}

Does the current market support holding this position?
If SL/TP are missing, mention that explicitly.
Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"market_score": <0-100>, "analysis": "<your evaluation>"}}
"""

    def _build_setup_prompt(self, idea: dict, ticker: dict, market: dict) -> str:
        symbol    = idea.get('symbol', '')
        direction = idea.get('direction', '')
        price     = ticker.get('price', '?')
        change    = ticker.get('change_24h', 0)
        funding   = ticker.get('funding_rate', '?')
        atr       = ticker.get('atr', '?')
        regime    = ticker.get('market_regime', 'UNKNOWN')
        btc       = market.get('btc') or {}

        return f"""You are a market analyst evaluating a NEW trade setup.

SETUP: {symbol} {direction}
Current price: {price}, 24h change: {change}%, Funding: {funding}, ATR: {atr}, Regime: {regime}
BTC: {btc.get('price', '?')}

Is the market environment favorable for this setup?
Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"market_score": <0-100>, "analysis": "<your evaluation>"}}
"""

    def _build_general_market_prompt(self, market: dict) -> str:
        btc    = market.get('btc') or {}
        eth    = market.get('eth') or {}
        trend  = market.get('trend', 'NEUTRAL')
        regime = market.get('market_regime', 'UNKNOWN')

        return f"""Brief market overview in Russian (2-3 sentences).
BTC: {btc.get('price', '?')}, ETH: {eth.get('price', '?')}, Trend: {trend}, Regime: {regime}.
Return ONLY valid JSON: {{"market_score": <0-100>, "analysis": "<your overview>"}}"""
