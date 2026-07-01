"""
ai/consensus_engine.py
Refactored consensus engine with parallel agent execution,
degraded state support, honest confidence scoring,
and market_trend extraction.
"""

import asyncio
import json
import logging
import re
from ai.agents.market_agent import MarketAgent
from ai.agents.risk_agent import RiskAgent
from ai.agents.psychology_agent import PsychologyAgent
from ai.agents.judge_agent import JudgeAgent
from ai.context_builder import ContextBuilder
from ai.trade_scorer import TradeScorer

logger = logging.getLogger(__name__)

AGENT_TIMEOUT = 30          # per-agent timeout
CONSENSUS_TIMEOUT = 45      # total consensus timeout
DEGRADED_SCORE = 50         # fallback score when agent fails


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
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны. Попробуйте позже.")
        logger.info(f"CONSENSUS ENGINE: analyzing position {position.get('symbol')}")
        return await self._run_agents_parallel(context, 'open')

    async def analyze_new_setup(self, ticker: str, direction: str, extra_notes: str = '') -> dict:
        context = await self.context_builder.build_for_new_setup(ticker, direction, extra_notes)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны. Попробуйте позже.")
        logger.info(f"CONSENSUS ENGINE: analyzing setup {ticker} {direction}")
        return await self._run_agents_parallel(context, 'setup')

    async def analyze_closed_trade(self, trade: dict) -> dict:
        score_result = self.scorer.score(trade)
        context = await self.context_builder.build_for_closed_trade(trade, score_result)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны. Попробуйте позже.")
        logger.info(f"CONSENSUS ENGINE: analyzing closed trade {trade.get('symbol')}")
        return await self._run_agents_parallel(context, 'post_trade')

    async def _run_agents_parallel(self, context: dict, mode: str) -> dict:
        """
        Run Market, Risk, Psychology agents in parallel,
        then JudgeAgent with aggregated results.
        Returns result dict with degraded flag and market_trend.
        """
        degraded = False
        degraded_agents = []

        # Helper to run one agent safely
        async def _run_agent(name: str, agent, ctx: dict):
            try:
                result = await asyncio.wait_for(agent.analyze(ctx), timeout=AGENT_TIMEOUT)
                parsed = self._parse_agent_response(result, name)
                return parsed
            except asyncio.TimeoutError:
                logger.warning(f"{name} timed out after {AGENT_TIMEOUT}s")
                return self._degraded_result(name, "timeout")
            except Exception as e:
                logger.error(f"{name} failed: {e}")
                return self._degraded_result(name, str(e))

        # Run all three agents in parallel with total timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    _run_agent("MarketAgent", self.market, context),
                    _run_agent("RiskAgent", self.risk, context),
                    _run_agent("PsychologistAgent", self.psych, context),
                ),
                timeout=CONSENSUS_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.error("Consensus timed out after %ds", CONSENSUS_TIMEOUT)
            return self._error_response("Анализ превысил допустимое время. Попробуйте позже.")

        market_result, risk_result, psych_result = results

        # Check degradation
        for res in [market_result, risk_result, psych_result]:
            if res.get('degraded'):
                degraded = True
                degraded_agents.append(res.get('agent_name', 'unknown'))

        # Extract texts and scores
        market_text = market_result.get('text', str(market_result))
        risk_text = risk_result.get('text', str(risk_result))
        psych_text = psych_result.get('text', str(psych_result))

        market_score = market_result.get('score', DEGRADED_SCORE)
        risk_score = risk_result.get('score', DEGRADED_SCORE)
        psych_score = psych_result.get('score', DEGRADED_SCORE)

        # Calculate trade score from TradeScorer
        trade_score = None
        position = context.get('position')
        trade = context.get('trade')
        if position:
            try:
                score_result = self.scorer.score_open_position(position)
                trade_score = score_result.get('total_score', 5) * 10
            except Exception as e:
                logger.warning(f"TradeScorer (open position) failed: {e}")
        elif trade:
            try:
                score_result = self.scorer.score(trade)
                trade_score = score_result.get('total_score', 5) * 10
            except Exception as e:
                logger.warning(f"TradeScorer (closed trade) failed: {e}")

        # JudgeAgent with raw JSON strings for compatibility
        try:
            verdict = await asyncio.wait_for(
                self.judge.synthesize(
                    json.dumps(market_result.get('raw', {})),
                    json.dumps(risk_result.get('raw', {})),
                    json.dumps(psych_result.get('raw', {})),
                    mode=mode,
                    trade_score=trade_score
                ),
                timeout=AGENT_TIMEOUT
            )
        except Exception as e:
            logger.error(f"JudgeAgent failed: {e}")
            verdict = json.dumps({
                "final_score": 0,
                "verdict": "AVOID",
                "summary": f"Ошибка JudgeAgent: {e}"
            })
            degraded = True
            degraded_agents.append("JudgeAgent")

        # --- Determine market_trend ---
        market_trend = self._extract_market_trend(market_text, context)

        # Calculate honest metrics
        data_quality = self._calculate_data_quality(context)
        agent_confidences = [
            c for c in [
                market_result.get('confidence'),
                risk_result.get('confidence'),
                psych_result.get('confidence')
            ] if c is not None
        ]
        avg_agent_confidence = sum(agent_confidences) / len(agent_confidences) if agent_confidences else 0.5

        # Confidence = average of agent confidences × data_quality, penalized by degradation
        confidence = avg_agent_confidence * data_quality
        if degraded:
            confidence *= 0.7  # penalty for degraded agents
        confidence = max(0.1, min(1.0, confidence))

        disagreement = self._calculate_disagreement(
            json.dumps(market_result.get('raw', {})),
            json.dumps(risk_result.get('raw', {})),
            json.dumps(psych_result.get('raw', {}))
        )

        memory = context.get('memory', '')

        # Build degraded notice
        if degraded:
            memory = f"⚠️ Анализ неполный. Отказали: {', '.join(degraded_agents)}.\n" + memory

        return {
            'market_review': market_text,
            'risk_review': risk_text,
            'psychology_review': psych_text,
            'market_trend': market_trend,
            'judge_verdict': verdict,
            'confidence': round(confidence, 2),
            'disagreement': round(disagreement, 2),
            'data_quality': round(data_quality, 2),
            'degraded': degraded,
            'memory': memory
        }

    def _extract_market_trend(self, market_text: str, context: dict) -> str:
        """
        Попытаться извлечь тренд из текста MarketAgent,
        если не удалось – использовать контекст рынка.
        """
        # Ищем ключевые слова в тексте агента
        text_lower = market_text.lower()
        if any(word in text_lower for word in ['bullish', 'бычий', 'восходящий', 'рост']):
            return "BULLISH"
        if any(word in text_lower for word in ['bearish', 'медвежий', 'нисходящий', 'падение']):
            return "BEARISH"
        if any(word in text_lower for word in ['sideways', 'нейтральный', 'боковик', 'консолидация']):
            return "SIDEWAYS"

        # Fallback к данным рынка
        market = context.get('market', {}) or {}
        trend = market.get('trend', '')
        if trend in ('BULLISH', 'BEARISH', 'SIDEWAYS'):
            return trend

        return "UNKNOWN"

    def _parse_agent_response(self, response: str, agent_name: str) -> dict:
        """Parse agent JSON response, extracting text, score, and confidence."""
        try:
            data = json.loads(response) if isinstance(response, str) else response
        except (json.JSONDecodeError, TypeError):
            return {
                'text': str(response),
                'score': DEGRADED_SCORE,
                'confidence': 0.3,
                'degraded': True,
                'agent_name': agent_name,
                'raw': {}
            }

        # Extract display text
        text = data.get('analysis') or data.get('summary') or str(data)

        # Extract score (market_score, risk_score, psychology_score)
        score = (
            data.get('market_score') or
            data.get('risk_score') or
            data.get('psychology_score') or
            DEGRADED_SCORE
        )

        # Extract confidence (if agent provides it)
        confidence = data.get('confidence', 0.5)

        return {
            'text': str(text),
            'score': int(score) if score else DEGRADED_SCORE,
            'confidence': float(confidence),
            'degraded': False,
            'agent_name': agent_name,
            'raw': data
        }

    def _degraded_result(self, agent_name: str, error: str) -> dict:
        """Create a degraded result for a failed agent."""
        return {
            'text': f"⚠️ {agent_name} недоступен: {error}",
            'score': DEGRADED_SCORE,
            'confidence': 0.0,
            'degraded': True,
            'agent_name': agent_name,
            'raw': {}
        }

    def _calculate_data_quality(self, context: dict) -> float:
        score = 0.0
        ticker = context.get('ticker')
        if ticker and (ticker.get('price', 0) or 0) > 0:
            score += 0.4
        market = context.get('market', {}) or {}
        btc = (market.get('btc') or {}) if market else {}
        if btc and (btc.get('price', 0) or 0) > 0:
            score += 0.3
        history = context.get('history', {}) or {}
        if history and (history.get('stats') or {}).get('total_trades', 0) > 0:
            score += 0.2
        portfolio = context.get('portfolio', {}) or {}
        if portfolio and (portfolio.get('balance') or 0) > 0:
            score += 0.1
        return min(1.0, score)

    def _calculate_disagreement(self, market: str, risk: str, psych: str) -> float:
        disagreement_score = 0.0
        market_lower = market.lower()
        risk_lower = risk.lower()
        psych_lower = psych.lower()

        if ('buy' in market_lower or 'bullish' in market_lower) and ('high' in risk_lower or 'extreme' in risk_lower):
            disagreement_score += 0.4
        if ('buy' in market_lower) and ('revenge' in psych_lower or 'tilt' in psych_lower or 'emotional' in psych_lower):
            disagreement_score += 0.3
        if ('safe' in risk_lower) and ('revenge' in psych_lower or 'tilt' in psych_lower):
            disagreement_score += 0.2
        if ('wait' in market_lower or 'wait' in risk_lower) and ('входить' in psych_lower or 'buy' in psych_lower):
            disagreement_score += 0.3

        return min(1.0, disagreement_score)

    def _is_market_data_valid(self, context: dict) -> bool:
        if context.get('idea') or context.get('ticker'):
            ticker = context.get('ticker')
            if ticker and ticker.get('price', 0) > 0:
                return True
            if context.get('idea'):
                return False

        market = context.get('market') or context.get('market_snapshot', {})
        btc = market.get('btc', {}) if market else {}
        eth = market.get('eth', {}) if market else {}
        if btc and btc.get('price', 0) > 0:
            return True
        if eth and eth.get('price', 0) > 0:
            return True
        return False

    def _error_response(self, message: str) -> dict:
        return {
            'market_review': message,
            'risk_review': message,
            'psychology_review': message,
            'market_trend': 'UNKNOWN',
            'judge_verdict': json.dumps({"verdict": "AVOID", "final_score": 0, "summary": message}),
            'confidence': 0.0,
            'disagreement': 0.0,
            'data_quality': 0.0,
            'degraded': True,
            'memory': ''
        }