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
from ai.engines.structure_arbiter import build_structure_plan
from ai.trade_scorer import TradeScorer
from services.market_data import get_market_snapshot
from services.bingx_api import get_balance
from services.calc_engine import calculate_position
from services.stop_engine import analyze_stop
from services.tp_engine import analyze_tp
from utils.liquidation import get_volatility_class

logger = logging.getLogger(__name__)

REQUEST_TYPES = ("open_position", "closed_trade", "new_setup")

_AGENTS_BY_TYPE = {
    "open_position": "Position Analyst + Risk Manager + Judge",
    "closed_trade":  "Trade Reviewer + Risk Manager + Judge",
    "new_setup":     "Market Analyst + Strategy Advisor + Risk Manager + Judge",
}

# Правила размера позиции/плеча по умолчанию для торгового плана нового
# сетапа (Этап 6) — те же, что уже показаны пользователю в закреплённом
# статусе (core/scheduler.py:_build_status_text, раздел "Правила"): вход
# 10% от депо, плечо x5 для BTC/ETH, x3 для остальных пар.
SETUP_RISK_PERCENT = 10.0
SETUP_LEVERAGE_LOW_VOL = 5
SETUP_LEVERAGE_OTHER = 3


class AIOrchestrator:
    """Маршрутизирует запросы пользователя к AI Trading Core."""

    def __init__(self, consensus: ConsensusEngine):
        self.consensus = consensus

    async def review_open_position(self, position: dict, user_id: str = "default") -> dict:
        """Полный разбор открытой позиции (Этап 4 плана AI Trading Core):
        качество входа и риск — от AI-консилиума (ConsensusEngine).
        Position Analyst / Trade Management (structure/stop/tp) больше не
        отдельный вердикт — его сигналы (override при пробое инвалидации
        или достижении полного TP, структурный компонент скора для
        остального) уже учтены внутри ConsensusEngine.analyze_open_position()
        → JudgeAgent.synthesize() (см. DECISION_FLOW_AUDIT.md, Вариант C).
        position_plan в result — тот же самый расчёт, что видел Judge, не
        повторный пересчёт отдельным путём (иначе можно было бы снова
        разойтись, если рыночные данные успели измениться между двумя
        вызовами)."""
        self._log("open_position")
        return await self.consensus.analyze_open_position(position, user_id=user_id)

    async def build_position_plan(self, position: dict) -> dict:
        """Публичный метод — используется core/scheduler.py:position_watch_job
        (Этап 7) для дешёвого структурного тика без полного LLM-консилиума.
        Делегирует в ai/engines/structure_arbiter.py:build_structure_plan() —
        единственное место, где считается position_plan, чтобы у watch_job и
        у ConsensusEngine.analyze_open_position() не было двух независимых
        реализаций одного и того же расчёта (см. DECISION_FLOW_AUDIT.md,
        Вариант C, требование 5)."""
        return await build_structure_plan(position)

    async def review_closed_trade(self, trade: dict, user_id: str = "default") -> dict:
        """Полный разбор закрытой сделки (Этап 5 плана AI Trading Core):
        качество входа/сопровождения/стопа/тейка и вердикт — от AI-консилиума
        (Trade Reviewer + Risk Manager + Judge); структурированная детальная
        оценка (RR, плечо, риск на сделку, дисциплина, психология,
        итоговый score/verdict) — от TradeScorer, единого источника для
        всех вызывающих мест (раньше auto_sync.py и core/router.py считали
        и/или сохраняли это независимо и по-разному — router.py вообще
        ссылался на несуществующий ключ 'ai_score' в ответе консилиума и
        никогда не сохранял оценку при ручном перезапуске анализа)."""
        self._log("closed_trade")
        result = await self.consensus.analyze_closed_trade(trade, user_id=user_id)
        # score_breakdown теперь считается один раз внутри ConsensusEngine —
        # с реальным balance из уже собранного контекста (TRADER_DNA_V1.md
        # §1.1, DNA v2). Раньше здесь заново вызывался TradeScorer.score(trade)
        # без context, что молча перезаписывало корректную оценку нейтральной.
        # Фолбэк без context остаётся только на случай _error_response()
        # (например, недоступны рыночные данные) — тогда score_breakdown в
        # result отсутствует.
        result["score_breakdown"] = result.get("score_breakdown") or TradeScorer.score(trade)
        return result

    async def evaluate_new_setup(self, ticker: str, direction: str, extra_notes: str = "", user_id: str = "default") -> dict:
        """Полный торговый план по новому сетапу (Этап 6 плана AI Trading Core):
        сценарий/аргументация/риск — от AI-консилиума (Market Analyst +
        Strategy Advisor + Risk Manager + Judge); конкретные цифры (цена
        входа, Stop Loss, TP1-3, Risk/Reward, размер позиции) — от
        детерминированных structure/stop/tp/calc_engine на гипотетической
        позиции по текущей рыночной цене."""
        self._log("new_setup")
        result = await self.consensus.analyze_new_setup(ticker, direction, extra_notes=extra_notes, user_id=user_id)
        result["trade_plan"] = await self._build_setup_plan(ticker, direction)
        return result

    async def _build_setup_plan(self, ticker: str, direction: str) -> dict:
        symbol = ticker if ticker.endswith("-USDT") else f"{ticker}-USDT"
        side = "LONG" if direction.upper() in ("LONG", "BUY", "ЛОНГ") else "SHORT"
        try:
            snapshot = await get_market_snapshot(symbol)
            entry_price = snapshot.get("price", 0)
            if entry_price <= 0:
                return {}

            # Гипотетическая позиция "как если бы вошли прямо сейчас" — те же
            # движки, что уже используются для сопровождения открытых позиций
            # (Этап 4), дают структурные SL/TP и для ещё не открытой сделки.
            hypothetical = {
                "symbol": symbol, "side": side, "entry_price": entry_price,
                "unrealized_pnl": 0, "quantity": 0, "leverage": 1,
            }
            stop = analyze_stop(snapshot, hypothetical)
            tp = analyze_tp(snapshot, hypothetical)

            stop_loss = stop.get("hard_sl")
            tp1, tp2, tp3 = tp.get("tp1"), tp.get("tp2"), tp.get("runner")

            risk_reward = None
            if stop_loss and tp1 and stop_loss != entry_price:
                risk_reward = round(abs(tp1 - entry_price) / abs(entry_price - stop_loss), 2)

            leverage = SETUP_LEVERAGE_LOW_VOL if get_volatility_class(symbol) == "LOW" else SETUP_LEVERAGE_OTHER

            position_calc = {}
            try:
                balance_result = await get_balance()
                balance = balance_result.get("equity", 0) if balance_result.get("success") else 0
                if balance > 0:
                    position_calc = calculate_position(
                        symbol, entry_price, leverage, balance,
                        risk_percent=SETUP_RISK_PERCENT, margin_type="cross"
                    )
            except Exception as e:
                logger.warning(f"AI Orchestrator: не удалось получить баланс для расчёта позиции {symbol}: {e}")

            return {
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "risk_reward": risk_reward,
                "leverage": leverage,
                "position_size": position_calc.get("position_size"),
                "margin": position_calc.get("margin"),
                "notional": position_calc.get("notional"),
            }
        except Exception as e:
            logger.warning(f"AI Orchestrator: не удалось построить trade_plan для {ticker}: {e}")
            return {}

    async def handle(self, request_type: str, **kwargs) -> dict:
        """Универсальный диспетчер по строковому типу запроса — для вызывающего
        кода, который сам не знает заранее, какой метод вызывать (например,
        будущий парсер свободного текста)."""
        user_id = kwargs.get("user_id", "default")
        if request_type == "open_position":
            return await self.review_open_position(kwargs["position"], user_id=user_id)
        if request_type == "closed_trade":
            return await self.review_closed_trade(kwargs["trade"], user_id=user_id)
        if request_type == "new_setup":
            return await self.evaluate_new_setup(
                kwargs["ticker"], kwargs["direction"], extra_notes=kwargs.get("extra_notes", ""), user_id=user_id
            )
        raise ValueError(f"AIOrchestrator: неизвестный тип запроса '{request_type}', ожидается один из {REQUEST_TYPES}")

    @staticmethod
    def _log(request_type: str) -> None:
        logger.info(f"AI Orchestrator: запрос type={request_type} -> {_AGENTS_BY_TYPE[request_type]}")
