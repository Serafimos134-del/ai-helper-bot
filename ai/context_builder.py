import logging
from services.database import Database
from services.bingx_api import get_balance, get_open_positions, get_ticker, get_top_tickers

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Собирает структурированный контекст для AI-агентов."""

    def __init__(self):
        self.db = Database()

    def build_full_context(self) -> dict:
        """Собирает полный контекст: рынок + портфель + история."""
        return {
            "market": self._build_market_context(),
            "portfolio": self._build_portfolio_context(),
            "history": self._build_history_context(),
        }

    def _build_market_context(self) -> dict:
        """Рыночный контекст: BTC, ETH, топ-5, тренд."""
        context = {"btc": None, "eth": None, "top_movers": [], "trend": "NEUTRAL"}

        try:
            btc = get_ticker("BTC-USDT")
            if btc.get("success"):
                t = btc["ticker"]
                context["btc"] = {
                    "price": float(t.get("lastPrice", 0)),
                    "change_24h": float(t.get("priceChangePercent", 0)),
                    "high": float(t.get("highPrice", 0)),
                    "low": float(t.get("lowPrice", 0)),
                    "volume": float(t.get("quoteVolume", 0)),
                }
        except Exception as e:
            logger.error(f"Ошибка получения BTC: {e}")

        try:
            eth = get_ticker("ETH-USDT")
            if eth.get("success"):
                t = eth["ticker"]
                context["eth"] = {
                    "price": float(t.get("lastPrice", 0)),
                    "change_24h": float(t.get("priceChangePercent", 0)),
                    "high": float(t.get("highPrice", 0)),
                    "low": float(t.get("lowPrice", 0)),
                    "volume": float(t.get("quoteVolume", 0)),
                }
        except Exception as e:
            logger.error(f"Ошибка получения ETH: {e}")

        try:
            top = get_top_tickers(5)
            if top.get("success"):
                for t in top["tickers"]:
                    context["top_movers"].append({
                        "symbol": t.get("symbol", ""),
                        "change": float(t.get("priceChangePercent", 0)),
                        "volume": float(t.get("quoteVolume", 0)),
                    })
        except Exception as e:
            logger.error(f"Ошибка получения топ-5: {e}")

        if context["btc"] and context["btc"]["change_24h"] > 1:
            context["trend"] = "BULLISH"
        elif context["btc"] and context["btc"]["change_24h"] < -1:
            context["trend"] = "BEARISH"

        return context

    def _build_portfolio_context(self) -> dict:
        """Контекст портфеля: баланс, открытые позиции, exposure."""
        context = {
            "balance": None,
            "available": 0,
            "used_margin": 0,
            "unrealized_pnl": 0,
            "open_positions": [],
            "position_count": 0,
        }

        try:
            balance = get_balance()
            if balance.get("success"):
                context["balance"] = balance["equity"]
                context["available"] = balance["available"]
                context["used_margin"] = balance["used_margin"]
                context["unrealized_pnl"] = balance["unrealized_pnl"]
        except Exception as e:
            logger.error(f"Ошибка получения баланса: {e}")

        try:
            positions = get_open_positions()
            if positions.get("success"):
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
        except Exception as e:
            logger.error(f"Ошибка получения позиций: {e}")

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

            # Считаем текущие серии
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