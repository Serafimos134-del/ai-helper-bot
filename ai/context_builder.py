import asyncio
import logging
from services.database import Database
from services.bingx_api import (
    get_balance, get_open_positions, get_ticker, get_top_tickers,
    get_funding_rate, get_open_interest, get_kline,
    _calculate_atr, _detect_market_regime
)
from ai.trader_context import build_trader_context, format_trader_context_summary
from ai.risk_profile import build_risk_profile_context

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Собирает структурированный контекст для AI-агентов."""

    def __init__(self):
        self.db = Database()

    async def build_full_context(self) -> dict:
        market, portfolio, history = await asyncio.gather(
            self._build_market_context(),
            self._build_portfolio_context(),
            asyncio.to_thread(self._build_history_context)
        )
        return {"market": market, "portfolio": portfolio, "history": history}

    async def _build_market_context(self) -> dict:
        context = {"btc": None, "eth": None, "top_movers": [], "trend": "NEUTRAL", "market_regime": "UNKNOWN"}

        btc = await get_ticker("BTC-USDT")
        eth = await get_ticker("ETH-USDT")
        top = await get_top_tickers(5)

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

        klines = await get_kline("BTC-USDT", "1h", 24)
        if klines and klines.get("success"):
            context["market_regime"] = _detect_market_regime(klines["klines"])

        logger.info(f"CONTEXT BUILDER (market): btc={context.get('btc')}, eth={context.get('eth')}")
        return context

    async def _build_portfolio_context(self) -> dict:
        context = {
            "balance": None, "available": 0, "used_margin": 0,
            "unrealized_pnl": 0, "open_positions": [], "position_count": 0,
        }

        balance = await get_balance()
        positions = await get_open_positions()

        if balance and balance.get("success"):
            context["balance"] = balance["equity"]
            context["available"] = balance["available"]
            context["used_margin"] = balance["used_margin"]
            context["unrealized_pnl"] = balance["unrealized_pnl"]

        if positions and positions.get("success"):
            for p in positions.get("trades", []):
                entry_price = float(p.get("entryPrice", 0))
                if entry_price <= 0:
                    continue
                size = abs(float(p.get("positionAmt", p.get("size", 0))))
                unrealized_pnl = float(p.get("unrealizedPnl", 0))
                side = p.get("side", "LONG")
                # Оценочная текущая цена на основе PnL (с учётом направления)
                if size > 0:
                    if side == "LONG":
                        current_price = entry_price + (unrealized_pnl / size)
                    else:
                        current_price = entry_price - (unrealized_pnl / size)
                else:
                    current_price = entry_price

                context["open_positions"].append({
                    "symbol": p.get("symbol", ""),
                    "side": side,
                    "entry_price": entry_price,
                    "unrealized_pnl": unrealized_pnl,
                    "leverage": p.get("leverage", 1),
                    "size": size,
                    "current_price": current_price,
                    "stop_loss": p.get("stopLoss"),
                    "take_profit": p.get("takeProfit"),
                })
            context["position_count"] = len(context["open_positions"])

        return context

    def _build_history_context(self, user_id: str = 'default') -> dict:
        # Раньше здесь же считался revenge_score/fomo_score/overtrading_score/
        # premature_exit_score/tilt_probability (_calculate_behavior_metrics) —
        # четвёртая независимая реализация поведенческого скоринга поверх
        # BehaviorEngine и PsychologyEngine.assess(), и при этом мёртвая: эти
        # поля читались только в _rule_based_analysis(), которая не вызывается
        # ни для одного из трёх реальных режимов консилиума (mode всегда один
        # из open/post_trade/setup). Удалено при консолидации в TraderContext
        # (см. TRADER_INTELLIGENCE_ARCHITECTURE.md, §1.3/§4, Этап 5) — та же
        # личная история теперь доходит до Judge через TraderContext, причём
        # реально, а не в недостижимую ветку.
        context = {
            "stats": None, "recent_trades": [],
            "losing_streak": 0, "winning_streak": 0,
        }

        try:
            stats = self.db.get_stats(user_id=user_id)
            context["stats"] = stats
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")

        try:
            trades = self.db.get_closed_trades(limit=50, user_id=user_id)
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

    async def build_for_open_position(self, position: dict, user_id: str = "default") -> dict:
        ticker_info = None
        symbol = position.get("symbol", "")
        if symbol:
            ticker_info = await self._build_ticker_info(symbol)

        market, portfolio, history, trader_context, risk_profile = await asyncio.gather(
            self._build_market_context(),
            self._build_portfolio_context(),
            asyncio.to_thread(self._build_history_context, user_id),
            asyncio.to_thread(build_trader_context, self.db, symbol, user_id),
            asyncio.to_thread(build_risk_profile_context, self.db, user_id),
        )

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
            "memory": format_trader_context_summary(trader_context),
            "trader_context": trader_context,
            "risk_profile": risk_profile,
        }

    async def build_for_new_setup(self, ticker: str, direction: str, extra_notes: str = "", user_id: str = "default") -> dict:
        symbol = ticker
        if not symbol.endswith("-USDT"):
            symbol = f"{ticker}-USDT"

        ticker_info = await self._build_ticker_info(symbol)
        market, portfolio, history, trader_context, risk_profile = await asyncio.gather(
            self._build_market_context(),
            self._build_portfolio_context(),
            asyncio.to_thread(self._build_history_context, user_id),
            asyncio.to_thread(build_trader_context, self.db, symbol, user_id),
            asyncio.to_thread(build_risk_profile_context, self.db, user_id),
        )

        logger.info(f"CONTEXT BUILDER (setup): ticker_info={ticker_info}, market_trend={market.get('trend')}")

        return {
            "idea": {"ticker": ticker, "symbol": symbol, "direction": direction, "notes": extra_notes},
            "ticker": ticker_info,
            "market": market,
            "portfolio": portfolio,
            "history": history,
            "memory": format_trader_context_summary(trader_context),
            "trader_context": trader_context,
            "risk_profile": risk_profile,
        }

    async def build_for_closed_trade(self, trade: dict, score_result: dict = None, user_id: str = "default") -> dict:
        symbol = trade.get("symbol", "")
        # portfolio (баланс) раньше здесь не собирался вообще — в отличие от
        # build_for_open_position/build_for_new_setup. Из-за этого
        # TradeScorer.score() у закрытой сделки не мог получить реальный
        # balance даже если бы вызывающий код его передавал (см.
        # TRADER_DNA_V1.md §1.1, DNA v2) — context.get("balance") всегда был
        # 0 просто потому, что баланс никогда не запрашивался для этого пути.
        market, portfolio, history, trader_context, risk_profile = await asyncio.gather(
            self._build_market_context(),
            self._build_portfolio_context(),
            asyncio.to_thread(self._build_history_context, user_id),
            asyncio.to_thread(build_trader_context, self.db, symbol, user_id),
            asyncio.to_thread(build_risk_profile_context, self.db, user_id),
        )

        return {
            "trade": {
                "symbol": symbol,
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
            "portfolio": portfolio,
            "history": history,
            "memory": format_trader_context_summary(trader_context),
            "trader_context": trader_context,
            "risk_profile": risk_profile,
        }

    async def _build_ticker_info(self, symbol: str) -> dict:
        info = {}
        try:
            ticker_res = await get_ticker(symbol)
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
            funding = await get_funding_rate(symbol)
            if funding and funding.get("success"):
                info["funding_rate"] = funding["funding_rate"]
                info["mark_price"] = funding.get("mark_price", 0)
        except Exception as e:
            logger.error(f"Ошибка получения funding rate {symbol}: {e}")

        try:
            oi = await get_open_interest(symbol)
            if oi and oi.get("success"):
                info["open_interest"] = oi["open_interest"]
        except Exception as e:
            logger.error(f"Ошибка получения OI {symbol}: {e}")

        try:
            klines = await get_kline(symbol, "1h", 24)
            if klines and klines.get("success"):
                info["atr"] = round(_calculate_atr(klines["klines"], 14), 4)
                info["market_regime"] = _detect_market_regime(klines["klines"])
        except Exception as e:
            logger.error(f"Ошибка расчёта ATR/regime {symbol}: {e}")

        return info if info else None

    # _get_memory_context_sync()/_get_best_ticker()/_get_best_direction()
    # удалены при консолидации в TraderContext (Этап 5, см.
    # TRADER_INTELLIGENCE_ARCHITECTURE.md, §8) — дублировали PerformanceEngine
    # через отдельные инкрементальные счётчики MemoryEngine в trader_memory.
    # Футер "ПРОФИЛЬ ТРЕЙДЕРА" теперь строит
    # ai/trader_context.py:format_trader_context_summary() из того же
    # TraderContext, что видит JudgeAgent — один источник вместо двух.