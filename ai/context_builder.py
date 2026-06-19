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
        loop = asyncio.get_running_loop()
        market_task = loop.run_in_executor(None, self._build_market_context)
        portfolio_task = loop.run_in_executor(None, self._build_portfolio_context)
        history_task = loop.run_in_executor(None, self._build_history_context)
        market, portfolio, history = await asyncio.gather(market_task, portfolio_task, history_task)
        return {"market": market, "portfolio": portfolio, "history": history}

    def _build_market_context(self) -> dict:
        context = {"btc": None, "eth": None, "top_movers": [], "trend": "NEUTRAL", "market_regime": "UNKNOWN"}

        btc = get_ticker("BTC-USDT")
        eth = get_ticker("ETH-USDT")
        top = get_top_tickers(5)

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

        klines = get_kline("BTC-USDT", "1h", 24)
        if klines and klines.get("success"):
            context["market_regime"] = _detect_market_regime(klines["klines"])

        logger.info(f"CONTEXT BUILDER (market): btc={context.get('btc')}, eth={context.get('eth')}")
        return context

    def _build_portfolio_context(self) -> dict:
        context = {
            "balance": None, "available": 0, "used_margin": 0,
            "unrealized_pnl": 0, "open_positions": [], "position_count": 0,
        }

        balance = get_balance()
        positions = get_open_positions()

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

    def _calculate_behavior_metrics(self, trades: list, losing_streak: int) -> dict:
        result = {
            "revenge_score": 0, "fomo_score": 0,
            "overtrading_score": 0, "premature_exit_score": 0,
            "tilt_probability": 0,
        }

        if not trades:
            return result

        recent = trades[:10]

        if losing_streak >= 2:
            result["revenge_score"] += min(losing_streak * 2, 6)
            leverages = [float(t.get("leverage", 1)) for t in recent]
            sizes = [abs(float(t.get("pnl", 0))) for t in recent]
            if len(leverages) >= 2 and leverages[0] > leverages[-1]:
                result["revenge_score"] += 2
            if len(sizes) >= 2 and sizes[0] > sizes[-1]:
                result["revenge_score"] += 2

        if len(recent) >= 3:
            try:
                times = [t.get("close_time", "") for t in recent if t.get("close_time")]
                if len(times) >= 3:
                    from datetime import datetime
                    parsed = []
                    for ts in times:
                        try:
                            parsed.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                        except:
                            pass
                    if len(parsed) >= 3:
                        time_range_hours = (parsed[0] - parsed[-1]).total_seconds() / 3600
                        if time_range_hours > 0:
                            trades_per_hour = len(parsed) / time_range_hours
                            if trades_per_hour > 2:
                                result["fomo_score"] += 4
                            elif trades_per_hour > 1:
                                result["fomo_score"] += 2
            except:
                pass

        if len(recent) >= 5:
            result["overtrading_score"] = min(len(recent), 10)

        profitable = [t for t in recent if float(t.get("pnl", 0)) > 0]
        if profitable:
            premature_count = 0
            for t in profitable:
                pnl = float(t.get("pnl", 0))
                entry = float(t.get("entry", 0))
                exit_price = float(t.get("exit", 0))
                if entry > 0 and exit_price > 0:
                    profit_pct = abs(exit_price - entry) / entry * 100
                    if profit_pct < 2 and pnl > 0:
                        premature_count += 1
            result["premature_exit_score"] = min(premature_count * 2, 8)

        tilt = 0
        if result["revenge_score"] >= 6:
            tilt += 40
        elif result["revenge_score"] >= 4:
            tilt += 25
        if result["fomo_score"] >= 4:
            tilt += 30
        elif result["fomo_score"] >= 2:
            tilt += 15
        if result["overtrading_score"] >= 7:
            tilt += 20
        if result["premature_exit_score"] >= 6:
            tilt += 10
        result["tilt_probability"] = min(tilt, 100)

        return result

    async def build_for_open_position(self, position: dict) -> dict:
        loop = asyncio.get_running_loop()
        market_task = loop.run_in_executor(None, self._build_market_context)
        portfolio_task = loop.run_in_executor(None, self._build_portfolio_context)
        history_task = loop.run_in_executor(None, self._build_history_context)

        ticker_info = None
        symbol = position.get("symbol", "")
        if symbol:
            ticker_info = self._build_ticker_info(symbol)

        market, portfolio, history = await asyncio.gather(market_task, portfolio_task, history_task)
        memory_context = self._get_memory_context_sync()

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
            "memory": memory_context,
            "trader_profile": {
                "style": "trend/breakout",
                "holding_period": "up to 2 weeks",
                "risk_priority": "position size > leverage",
            },
        }

    async def build_for_new_setup(self, ticker: str, direction: str, extra_notes: str = "") -> dict:
        loop = asyncio.get_running_loop()
        market_task = loop.run_in_executor(None, self._build_market_context)
        portfolio_task = loop.run_in_executor(None, self._build_portfolio_context)
        history_task = loop.run_in_executor(None, self._build_history_context)

        symbol = ticker
        if not symbol.endswith("-USDT"):
            symbol = f"{ticker}-USDT"

        ticker_info = self._build_ticker_info(symbol)
        market, portfolio, history = await asyncio.gather(market_task, portfolio_task, history_task)
        memory_context = self._get_memory_context_sync()

        logger.info(f"CONTEXT BUILDER (setup): ticker_info={ticker_info}, market_trend={market.get('trend')}")

        return {
            "idea": {"ticker": ticker, "symbol": symbol, "direction": direction, "notes": extra_notes},
            "ticker": ticker_info,
            "market": market,
            "portfolio": portfolio,
            "history": history,
            "memory": memory_context,
            "trader_profile": {
                "style": "trend/breakout",
                "holding_period": "up to 2 weeks",
                "risk_priority": "position size > leverage",
            },
        }

    async def build_for_closed_trade(self, trade: dict, score_result: dict = None) -> dict:
        loop = asyncio.get_running_loop()
        history_task = loop.run_in_executor(None, self._build_history_context)
        market_task = loop.run_in_executor(None, self._build_market_context)
        history, market = await asyncio.gather(history_task, market_task)
        memory_context = self._get_memory_context_sync()

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
            "memory": memory_context,
            "trader_profile": {
                "style": "trend/breakout",
                "holding_period": "up to 2 weeks",
                "risk_priority": "position size > leverage",
            },
        }

    def _build_ticker_info(self, symbol: str) -> dict:
        info = {}
        try:
            ticker_res = get_ticker(symbol)
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
            funding = get_funding_rate(symbol)
            if funding and funding.get("success"):
                info["funding_rate"] = funding["funding_rate"]
                info["mark_price"] = funding.get("mark_price", 0)
        except Exception as e:
            logger.error(f"Ошибка получения funding rate {symbol}: {e}")

        try:
            oi = get_open_interest(symbol)
            if oi and oi.get("success"):
                info["open_interest"] = oi["open_interest"]
        except Exception as e:
            logger.error(f"Ошибка получения OI {symbol}: {e}")

        try:
            klines = get_kline(symbol, "1h", 24)
            if klines and klines.get("success"):
                info["atr"] = round(_calculate_atr(klines["klines"], 14), 4)
                info["market_regime"] = _detect_market_regime(klines["klines"])
        except Exception as e:
            logger.error(f"Ошибка расчёта ATR/regime {symbol}: {e}")

        return info if info else None

    def _get_memory_context_sync(self) -> str:
        """
        Возвращает строку профиля трейдера из БД (без циклических импортов).
        Использует прямые запросы к Database (memory_get/memory_get_all).
        """
        try:
            db = Database()
            total = int(db.memory_get('global', 'total_trades') or 0)
            if total < 2:
                return ""

            wins = int(db.memory_get('global', 'winning_trades') or 0)
            losses = int(db.memory_get('global', 'losing_trades') or 0)
            avg_min = float(db.memory_get('holding', 'avg_minutes') or 0)

            best_ticker = self._get_best_ticker(db)
            best_direction = self._get_best_direction(db)

            lines = ["ПРОФИЛЬ ТРЕЙДЕРА (на основе истории):"]
            lines.append(f"- Всего сделок: {total} (побед: {wins}, поражений: {losses})")
            if best_ticker:
                lines.append(f"- Лучший тикер: {best_ticker}")
            if best_direction:
                lines.append(f"- Лучшее направление: {best_direction}")
            if avg_min > 0:
                lines.append(f"- Среднее удержание: {avg_min:.0f} мин")
            return "\n".join(lines) + "\n\n"
        except Exception as e:
            logger.error(f"Ошибка получения memory_context: {e}")
            return ""

    def _get_best_ticker(self, db) -> str:
        tickers = db.memory_get_all('ticker')
        best_wr = -1
        best = None
        for key, value in tickers.items():
            if key.endswith('_total'):
                ticker = key.replace('_total', '')
                wins = int(tickers.get(f'{ticker}_wins', 0))
                total = int(value)
                if total >= 2:
                    wr = wins / total * 100
                    if wr > best_wr:
                        best_wr = wr
                        best = f"{ticker} (WR: {wr:.0f}%, {total} сделок)"
        return best

    def _get_best_direction(self, db) -> str:
        directions = db.memory_get_all('direction')
        for key, value in directions.items():
            if key.endswith('_total'):
                direction = key.replace('_total', '')
                wins = int(directions.get(f'{direction}_wins', 0))
                total = int(value)
                if total >= 2:
                    wr = wins / total * 100
                    return f"{direction} (WR: {wr:.0f}%, {total} сделок)"
        return None