import asyncio
import logging
from ai.agents.market_agent import MarketAgent
from ai.agents.risk_agent import RiskAgent
from ai.agents.psychology_agent import PsychologyAgent
from ai.agents.judge_agent import JudgeAgent
from ai.context_builder import ContextBuilder
from ai.trade_scorer import TradeScorer

logger = logging.getLogger(__name__)

class ConsensusEngine:
    def __init__(self):
        self.market = MarketAgent()
        self.risk = RiskAgent()
        self.psych = PsychologyAgent()
        self.judge = JudgeAgent()
        self.context_builder = ContextBuilder()
        self.scorer = TradeScorer()

    async def analyze_open_position(self, position: dict) -> dict:
        context = await self.context_builder.build_for_open_position(position)
        market, risk, psych = await asyncio.gather(
            self.market.analyze(context),
            self.risk.analyze(context),
            self.psych.analyze(context)
        )
        verdict = await self.judge.synthesize(market, risk, psych, mode='open')
        return {
            'market_review': market,
            'risk_review': risk,
            'psychology_review': psych,
            'judge_verdict': verdict
        }

    async def analyze_new_setup(self, ticker: str, direction: str, extra_notes: str = '') -> dict:
        context = await self.context_builder.build_for_new_setup(ticker, direction, extra_notes)
        market, risk, psych = await asyncio.gather(
            self.market.analyze(context),
            self.risk.analyze(context),
            self.psych.analyze(context)
        )
        verdict = await self.judge.synthesize(market, risk, psych, mode='setup')
        return {
            'market_review': market,
            'risk_review': risk,
            'psychology_review': psych,
            'judge_verdict': verdict
        }

    async def analyze_closed_trade(self, trade: dict) -> dict:
        score_result = self.scorer.score(trade)
        context = await self.context_builder.build_for_closed_trade(trade, score_result)
        market, risk, psych = await asyncio.gather(
            self.market.analyze(context),
            self.risk.analyze(context),
            self.psych.analyze(context)
        )
        verdict = await self.judge.synthesize(market, risk, psych, mode='post_trade')
        return {
            'trade_score': score_result['total_score'],
            'market_review': market,
            'risk_review': risk,
            'psychology_review': psych,
            'judge_verdict': verdict
        }