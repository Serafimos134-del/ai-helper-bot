import asyncio
import json
import logging
from ai.agents.market_agent import MarketAgent
from ai.agents.risk_agent import RiskAgent
from ai.agents.psychology_agent import PsychologyAgent
from ai.agents.judge_agent import JudgeAgent
from ai.context_builder import ContextBuilder
from ai.trade_scorer import TradeScorer

logger = logging.getLogger(__name__)

AGENT_TIMEOUT = 30  # секунд на каждого агента

class ConsensusEngine:
    def __init__(self, provider):
        self.market = MarketAgent(provider)
        self.risk = RiskAgent(provider)
        self.psych = PsychologyAgent(provider)
        self.judge = JudgeAgent(provider)
        self.context_builder = ContextBuilder()
        self.scorer = TradeScorer()

    async def analyze_open_position(self, position: dict) -> dict:
        context = await self.context_builder.build_for_open_position(position)
        logger.info(f"CONSENSUS ENGINE: analyzing position {position.get('symbol')}")
        results = await asyncio.gather(
            asyncio.wait_for(self.market.analyze(context), timeout=AGENT_TIMEOUT),
            asyncio.wait_for(self.risk.analyze(context), timeout=AGENT_TIMEOUT),
            asyncio.wait_for(self.psych.analyze(context), timeout=AGENT_TIMEOUT),
            return_exceptions=True
        )
        market = results[0] if not isinstance(results[0], Exception) else f"Ошибка MarketAgent: {results[0]}"
        raw_risk = results[1] if not isinstance(results[1], Exception) else '{"summary": "Ошибка RiskAgent"}'
        psych = results[2] if not isinstance(results[2], Exception) else f"Ошибка PsychologyAgent: {results[2]}"

        # Извлекаем summary из JSON ответа RiskAgent
        try:
            risk_data = json.loads(raw_risk)
            risk = risk_data.get('summary', raw_risk)
        except Exception:
            risk = raw_risk

        verdict = await asyncio.wait_for(self.judge.synthesize(market, risk, psych, mode='open'), timeout=AGENT_TIMEOUT)
        return {
            'market_review': str(market),
            'risk_review': str(risk),
            'psychology_review': str(psych),
            'judge_verdict': verdict
        }

    async def analyze_new_setup(self, ticker: str, direction: str, extra_notes: str = '') -> dict:
        context = await self.context_builder.build_for_new_setup(ticker, direction, extra_notes)
        logger.info(f"CONSENSUS ENGINE: analyzing setup {ticker} {direction}")
        results = await asyncio.gather(
            asyncio.wait_for(self.market.analyze(context), timeout=AGENT_TIMEOUT),
            asyncio.wait_for(self.risk.analyze(context), timeout=AGENT_TIMEOUT),
            asyncio.wait_for(self.psych.analyze(context), timeout=AGENT_TIMEOUT),
            return_exceptions=True
        )
        market = results[0] if not isinstance(results[0], Exception) else f"Ошибка MarketAgent: {results[0]}"
        raw_risk = results[1] if not isinstance(results[1], Exception) else '{"summary": "Ошибка RiskAgent"}'
        psych = results[2] if not isinstance(results[2], Exception) else f"Ошибка PsychologyAgent: {results[2]}"

        try:
            risk_data = json.loads(raw_risk)
            risk = risk_data.get('summary', raw_risk)
        except Exception:
            risk = raw_risk

        verdict = await asyncio.wait_for(self.judge.synthesize(market, risk, psych, mode='setup'), timeout=AGENT_TIMEOUT)
        return {
            'market_review': str(market),
            'risk_review': str(risk),
            'psychology_review': str(psych),
            'judge_verdict': verdict
        }

    async def analyze_closed_trade(self, trade: dict) -> dict:
        score_result = self.scorer.score(trade)
        context = await self.context_builder.build_for_closed_trade(trade, score_result)
        logger.info(f"CONSENSUS ENGINE: analyzing closed trade {trade.get('symbol')}")
        results = await asyncio.gather(
            asyncio.wait_for(self.market.analyze(context), timeout=AGENT_TIMEOUT),
            asyncio.wait_for(self.risk.analyze(context), timeout=AGENT_TIMEOUT),
            asyncio.wait_for(self.psych.analyze(context), timeout=AGENT_TIMEOUT),
            return_exceptions=True
        )
        market = results[0] if not isinstance(results[0], Exception) else f"Ошибка MarketAgent: {results[0]}"
        raw_risk = results[1] if not isinstance(results[1], Exception) else '{"summary": "Ошибка RiskAgent"}'
        psych = results[2] if not isinstance(results[2], Exception) else f"Ошибка PsychologyAgent: {results[2]}"

        try:
            risk_data = json.loads(raw_risk)
            risk = risk_data.get('summary', raw_risk)
        except Exception:
            risk = raw_risk

        verdict = await asyncio.wait_for(self.judge.synthesize(market, risk, psych, mode='post_trade'), timeout=AGENT_TIMEOUT)
        return {
            'trade_score': score_result['total_score'],
            'market_review': str(market),
            'risk_review': str(risk),
            'psychology_review': str(psych),
            'judge_verdict': verdict
        }