"""
ai/orchestrator.py
AI Orchestrator — единая точка входа в AI Trading Core (см. AUDIT.md / план
"AI Trading Core", Этапы 2-3).

Определяет тип запроса и маршрутизирует его к нужному набору агентов:

  open_position  -> Position Analyst (MarketAgent, mode=open) + Risk Manager + Judge
  closed_trade   -> Trade Reviewer   (MarketAgent, mode=post_trade) + Risk Manager + Judge
  new_setup      -> Market Analyst + Strategy Advisor (MarketAgent, mode=setup) + Risk Manager + Judge

Роли Position Analyst / Trade Reviewer / Strategy Advisor из плана сейчас
реализованы не как отдельные классы, а как режимы (mode) внутри MarketAgent/
RiskAgent/PsychologyAgent (ConsensusEngine) — они уже дают нужное поведение
на каждый тип запроса. Выделять их в отдельные классы прямо сейчас означало
бы переименование без изменения поведения; Orchestrator документирует эту
маршрутизацию и даёт единую точку, куда такие классы можно будет подставить
позже (Portfolio/News/Macro Analyst и т.д.) без изменения вызывающего кода.
"""

import logging

from ai.consensus_engine import ConsensusEngine
from ai.engines.normalizer import normalize_position
from services.market_data import get_market_snapshot
from services.ai_decision_engine import analyze_decision

logger = logging.getLogger(__name__)

REQUEST_TYPES = ("open_position", "closed_trade", "new_setup")

_AGENTS_BY_TYPE = {
    "open_position": "Position Analyst + Risk Manager + Judge",
    "closed_trade":  "Trade Reviewer + Risk Manager + Judge",
    "new_setup":     "Market Analyst + Strategy Advisor + Risk Manager + Judge",
}


class AIOrchestrator:
    """Маршрутизирует запросы пользователя к AI Trading Core."""

    def __init__(self, consensus: ConsensusEngine):
        self.consensus = consensus

    async def review_open_position(self, position: dict) -> dict:
        """Полный разбор открытой позиции (Этап 4 плана AI Trading Core):
        качество входа и риск — от AI-консилиума (ConsensusEngine); актуальность
        стопа, перенос стопа, частичная фиксация прибыли и решение
        HOLD/EXIT/DCA/PARTIAL_TP/FULL_TP — от детерминированного
        ai_decision_engine (structure/stop/tp), который раньше существовал
        только за незарегистрированной командой /analyze."""
        self._log("open_position")
        result = await self.consensus.analyze_open_position(position)
        result["position_plan"] = await self._build_position_plan(position)
        return result

    async def _build_position_plan(self, position: dict) -> dict:
        symbol = position.get("symbol", "")
        if not symbol:
            return {}
        try:
            # ai_decision_engine/stop_engine/tp_engine ожидают нормализованные
            # snake_case поля (entry_price, unrealized_pnl, ...), а не сырой
            # ответ BingX API (entryPrice, unrealizedPnl, ...).
            normalized = normalize_position(position)
            # Trade Manager v2 поля (заданы через /setidea) не приходят из BingX
            # API — пробрасываем их с исходного объекта, если они там были
            # (например, позиция пришла из db.get_open_trades()).
            normalized["dca_count"] = position.get("dca_count", 0)
            normalized["invalidation_sl"] = position.get("invalidation_sl")
            normalized["tp_zones"] = position.get("tp_zones")

            snapshot = await get_market_snapshot(symbol)
            return analyze_decision(snapshot, normalized)
        except Exception as e:
            logger.warning(f"AI Orchestrator: не удалось построить position_plan для {symbol}: {e}")
            return {}

    async def review_closed_trade(self, trade: dict) -> dict:
        self._log("closed_trade")
        return await self.consensus.analyze_closed_trade(trade)

    async def evaluate_new_setup(self, ticker: str, direction: str, extra_notes: str = "") -> dict:
        self._log("new_setup")
        return await self.consensus.analyze_new_setup(ticker, direction, extra_notes=extra_notes)

    async def handle(self, request_type: str, **kwargs) -> dict:
        """Универсальный диспетчер по строковому типу запроса — для вызывающего
        кода, который сам не знает заранее, какой метод вызывать (например,
        будущий парсер свободного текста)."""
        if request_type == "open_position":
            return await self.review_open_position(kwargs["position"])
        if request_type == "closed_trade":
            return await self.review_closed_trade(kwargs["trade"])
        if request_type == "new_setup":
            return await self.evaluate_new_setup(
                kwargs["ticker"], kwargs["direction"], extra_notes=kwargs.get("extra_notes", "")
            )
        raise ValueError(f"AIOrchestrator: неизвестный тип запроса '{request_type}', ожидается один из {REQUEST_TYPES}")

    @staticmethod
    def _log(request_type: str) -> None:
        logger.info(f"AI Orchestrator: запрос type={request_type} -> {_AGENTS_BY_TYPE[request_type]}")
