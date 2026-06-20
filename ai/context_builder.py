import asyncio
import logging
from services.database import Database
from services.bingx_api import (
    get_balance, get_open_positions, get_ticker, get_top_tickers,
    get_funding_rate, get_open_interest, get_kline,
    _calculate_atr, _detect_market_regime
)

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
                size = abs(float(p.get("positionAmt", p.get("size", 0))))
                unrealized_pnl = float(p.get("unrealizedPnl", 0))
                # Оценочная текущая цена на основе PnL
                if size > 0:
                    current_price = entry_price + (unrealized_pnl / size)
                else:
                    current_price = entry_price

                context["open_positions"].append({
                    "symbol": p.get("symbol", ""),
                    "side": p.get("side", ""),
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

    def _build_history_context(self) -> dict:
        context = {
            "stats": None, "recent_trades": [],
            "losing_streak": 0, "winning_streak": 0,
            "revenge_score": 0, "fomo_score": 0,
            "overtrading_score": 0, "premature_exit_score": 0,
            "tilt_probability": 0,
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

            context.update(self._calculate_behavior_metrics(trades, context["losing_streak"]))

        except Exception as e:
            logger.error(f"Ошибка получения истории: {e}")

        return context

    # ... (методы _calculate_behavior_metrics, build_for_open_position, build_for_new_setup, build_for_closed_trade, _build_ticker_info, _get_memory_context_sync, _get_best_ticker, _get_best_direction без изменений) ...
    # Вставляем остальные методы из предыдущей полной версии, они не менялись. Важно: в конце файла не должно быть мусора.
