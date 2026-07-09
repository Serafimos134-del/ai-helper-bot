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
        self._log("open_position")
        return await self.consensus.analyze_open_position(position)

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
