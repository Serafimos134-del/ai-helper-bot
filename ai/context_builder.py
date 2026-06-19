import asyncio
import logging
from services.database import Database
from services.bingx_api import (
    get_balance, get_open_positions, get_ticker, get_top_tickers,
    get_funding_rate, get_open_interest, get_kline,
    _calculate_atr, _detect_market_regime
)
from services.api_cache import api_cache

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Собирает структурированный контекст для AI-агентов (с кэшированием API-запросов)."""

    def __init__(self):
        self.db = Database()

    async def build_full_context(self) -> dict:
        """Асинхронно собирает полный контекст: рынок + портфель + история (параллельно)."""
        loop = asyncio.get_running_loop()
        market_task = loop.run_in_executor(None, self._build_market_context)
        portfolio_task = loop.run_in_executor(None, self._build_portfolio_context)
        history_task = loop.run_in_executor(None, self._build_history_context)
        market, portfolio, history = await asyncio.gather(market_task, portfolio_task, history_task)
        return {
            "market": market,
            "portfolio": portfolio,
            "history": history,
        }

    def _build_market_context(self) -> dict:
        """Рыночный контекст: BTC, ETH, топ-5, тренд, режим рынка. С кэшированием."""
        context = {"btc": None, "eth": None, "top_movers": [], "trend": "NEUTRAL", "market_regime": "UNKNOWN"}

        btc = _sync_cache_get(get_ticker, "BTC-USDT")
        eth = _sync_cache_get(get_ticker, "ETH-USDT")
        top = _sync_cache_get(get_top_tickers, 5)

        if btc and btc.get("success"):
            t = btc["ticker"]
            context["btc"] = {
                "price": float(t.get("lastPrice", 0)),
                "change_24h": float(t.get("priceChangePercent", 0)),
                "high": float(t.get("highPrice", 0)),
                "low": float(t.get("lowPrice", 0)),
                "volume": float(t.get("quoteVolume", 0)),
            }

        if eth and eth.get("success"):
            t = eth["ticker"]
            context["eth"] = {
                "price": float(t.get("lastPrice", 0)),
                "change_24h": float(t.get("priceChangePercent", 0)),
                "high": float(t.get("highPrice", 0)),
                "low": float(t.get("lowPrice", 0)),
                "volume": float(t.get("quoteVolume", 0)),
            }

        if top and top.get("success"):
            for t in top["tickers"]:
                context["top_movers"].append({
                    "symbol": t.get("symbol", ""),
                    "change": float(t.get("priceChangePercent", 0)),
                    "volume": float(t.get("quoteVolume", 0)),
                })

        if context["btc"] and context["btc"]["change_24h"] > 1:
            context["trend"] = "BULLISH"
        elif context["btc"] and context["btc"]["change_24h"] < -1:
            context["trend"] = "BEARISH"

        # Определяем рыночный режим по BTC
        klines = _sync_cache_get(get_kline, "BTC-USDT", "1h", 24)
        if klines and klines.get("success"):
            context["market_regime"] = _detect_market_regime(klines["klines"])

        logger.info(f"CONTEXT BUILDER (market): btc={context.get('btc')}, eth={context.get('eth')}")
        return context

    def _build_portfolio_context(self) -> dict:
        """Контекст портфеля: баланс, открытые позиции, exposure. С кэшированием."""
        context = {
            "balance": None,
            "available": 0,
            "used_margin": 0,
            "unrealized_pnl": 0,
            "open_positions": [],
            "position_count": 0,
        }

        balance = _sync_cache_get(get_balance)
        positions = _sync_cache_get(get_open_positions)

        if balance and balance.get("success"):
            context["balance"] = balance["equity"]
            context["available"] = balance["available"]
            context["used_margin"] = balance["used_margin"]
            context["unrealized_pnl"] = balance["unrealized_pnl"]

        if positions and positions.get("success"):
            for p in positions.get("trades", []):
                context["open_positions"].append({
                    "symbol": p.get("symbol", ""),
                    "side": p.get("side", ""),
                    "entry_price": float(p.get("entryPrice", 0)),
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
                    "leverage": p.get("leverage", 1),
                    "size": abs(float(p.get("positionAmt", p.get("size", 0)))),
                })
            context["position_count"] = len(context["open_positions"])

        return context

    def _build_history_context(self) -> dict:
        """Контекст истории: статистика, последние сделки, серии."""
        context = {
            "stats": None,
            "recent_trades": [],
            "losing_streak": 0,
            "winning_streak": 0,
        }

        try:
            stats = self.db.get_stats()
            context["stats"] = stats
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")

        try:
            trades = self.db.get_closed_trades(limit=50)
            for t in trades:
                context["recent_trades"].append({
                    "symbol": t.get("symbol", ""),
                    "side": t.get("side", ""),
                    "pnl": float(t.get("realized_pnl", 0)),
                    "entry": float(t.get("entry_price", 0)),
                    "exit": float(t.get("exit_price", 0)),
                    "leverage": float(t.get("leverage", 1)),
                    "comment": t.get("exit_comment", t.get("comment", "")),
                    "close_time": str(t.get("close_time", "")),
                })

            pnls = [float(t.get("realized_pnl", 0)) for t in trades[:30]]
            for pnl in pnls:
                if pnl < 0:
                    context["losing_streak"] += 1
                else:
                    break
            for pnl in pnls:
                if pnl > 0:
                    context["winning_streak"] += 1
                else:
                    break
        except Exception as e:
            logger.error(f"Ошибка получения истории: {e}")

        return context

    # ────────────── Методы для Consensus Engine ──────────────

    async def build_for_open_position(self, position: dict) -> dict:
        """Контекст для анализа открытой позиции (расширенный: funding, OI, ATR, regime)."""
        loop = asyncio.get_running_loop()
        market_task = loop.run_in_executor(None, self._build_market_context)
        portfolio_task = loop.run_in_executor(None, self._build_portfolio_context)
        history_task = loop.run_in_executor(None, self._build_history_context)

        ticker_info = None
        symbol = position.get("symbol", "")
        if symbol:
            ticker_info = self._build_ticker_info(symbol)

        market, portfolio, history = await asyncio.gather(market_task, portfolio_task, history_task)

        return {
            "position": {
                "symbol": symbol,
                "side": position.get("side", ""),
                "entry_price": float(position.get("entry_price", position.get("entryPrice", 0))),
                "unrealized_pnl": float(position.get("unrealized_pnl", position.get("unrealizedPnl", 0))),
                "leverage": position.get("leverage", 1),
                "size": abs(float(position.get("quantity", position.get("positionAmt", 0)))),
                "stop_loss": position.get("stop_loss"),
                "take_profit": position.get("take_profit"),
            },
            "ticker": ticker_info,
            "market": market,
            "portfolio": portfolio,
            "history": history,
            "trader_profile": {
                "style": "trend/breakout",
                "holding_period": "up to 2 weeks",
                "risk_priority": "position size > leverage",
            },
        }

    async def build_for_new_setup(self, ticker: str, direction: str, extra_notes: str = "") -> dict:
        """Контекст для оценки нового сетапа (расширенный: funding, OI, ATR, regime)."""
        loop = asyncio.get_running_loop()
        market_task = loop.run_in_executor(None, self._build_market_context)
        portfolio_task = loop.run_in_executor(None, self._build_portfolio_context)
        history_task = loop.run_in_executor(None, self._build_history_context)

        symbol = ticker
        if not symbol.endswith("-USDT"):
            symbol = f"{ticker}-USDT"

        ticker_info = self._build_ticker_info(symbol)
        market, portfolio, history = await asyncio.gather(market_task, portfolio_task, history_task)

        logger.info(f"CONTEXT BUILDER (setup): ticker_info={ticker_info}, market_trend={market.get('trend')}")

        return {
            "idea": {
                "ticker": ticker,
                "symbol": symbol,
                "direction": direction,
                "notes": extra_notes,
            },
            "ticker": ticker_info,
            "market": market,
            "portfolio": portfolio,
            "history": history,
            "trader_profile": {
                "style": "trend/breakout",
                "holding_period": "up to 2 weeks",
                "risk_priority": "position size > leverage",
            },
        }

    async def build_for_closed_trade(self, trade: dict, score_result: dict = None) -> dict:
        """Контекст для post-trade анализа."""
        loop = asyncio.get_running_loop()
        history_task = loop.run_in_executor(None, self._build_history_context)
        market_task = loop.run_in_executor(None, self._build_market_context)
        history, market = await asyncio.gather(history_task, market_task)

        return {
            "trade": {
                "symbol": trade.get("symbol", ""),
                "side": trade.get("side", ""),
                "entry_price": float(trade.get("entry_price", 0)),
                "exit_price": float(trade.get("exit_price", 0)),
                "quantity": float(trade.get("quantity", 0)),
                "realized_pnl": float(trade.get("realized_pnl", 0)),
                "leverage": float(trade.get("leverage", 1)),
                "stop_loss": trade.get("stop_loss"),
                "take_profit": trade.get("take_profit"),
                "entry_comment": trade.get("entry_comment", ""),
                "exit_comment": trade.get("exit_comment", trade.get("comment", "")),
                "holding_minutes": trade.get("holding_minutes"),
                "ai_score": trade.get("ai_score"),
            },
            "score": score_result,
            "market": market,
            "history": history,
            "trader_profile": {
                "style": "trend/breakout",
                "holding_period": "up to 2 weeks",
                "risk_priority": "position size > leverage",
            },
        }

    def _build_ticker_info(self, symbol: str) -> dict:
        """
        Расширенная информация по инструменту:
        цена, изменение, high/low, funding rate, open interest, ATR, market regime.
        """
        info = {}
        try:
            ticker_res = _sync_cache_get(get_ticker, symbol)
            if ticker_res and ticker_res.get("success"):
                t = ticker_res["ticker"]
                info.update({
                    "price": float(t.get("lastPrice", 0)),
                    "change_24h": float(t.get("priceChangePercent", 0)),
                    "high": float(t.get("highPrice", 0)),
                    "low": float(t.get("lowPrice", 0)),
                    "volume": float(t.get("quoteVolume", 0)),
                })
        except Exception as e:
            logger.error(f"Ошибка получения тикера {symbol}: {e}")

        try:
            funding = _sync_cache_get(get_funding_rate, symbol)
            if funding and funding.get("success"):
                info["funding_rate"] = funding["funding_rate"]
                info["mark_price"] = funding.get("mark_price", 0)
        except Exception as e:
            logger.error(f"Ошибка получения funding rate {symbol}: {e}")

        try:
            oi = _sync_cache_get(get_open_interest, symbol)
            if oi and oi.get("success"):
                info["open_interest"] = oi["open_interest"]
        except Exception as e:
            logger.error(f"Ошибка получения OI {symbol}: {e}")

        try:
            klines = _sync_cache_get(get_kline, symbol, "1h", 24)
            if klines and klines.get("success"):
                info["atr"] = round(_calculate_atr(klines["klines"], 14), 4)
                info["market_regime"] = _detect_market_regime(klines["klines"])
        except Exception as e:
            logger.error(f"Ошибка расчёта ATR/regime {symbol}: {e}")

        return info if info else None


def _sync_cache_get(func, *args):
    """
    Синхронная обёртка для api_cache.get_or_fetch.
    Поскольку _build_* методы синхронны, но api_cache требует await,
    запускаем асинхронный вызов в отдельном event loop.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return func(*args)
        return loop.run_until_complete(api_cache.get_or_fetch(func, *args))
    except RuntimeError:
        return func(*args)