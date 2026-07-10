"""
ai/consensus_engine.py
Consensus engine with parallel agents, deterministic scoring,
normalizer integration, mode-aware dispatch, and real disagreement metric.
"""

import asyncio
import json
import logging
import math
from ai.agents.market_agent import MarketAgent
from ai.agents.risk_agent import RiskAgent
from ai.agents.psychology_agent import PsychologyAgent
from ai.agents.judge_agent import JudgeAgent
from ai.context_builder import ContextBuilder
from ai.trade_scorer import TradeScorer
from ai.engines.normalizer import normalize_position, normalize_trade
from ai.engines.scoring_engine import ScoringEngine
from ai.engines.structure_arbiter import build_structure_plan

logger = logging.getLogger(__name__)

AGENT_TIMEOUT    = 30
CONSENSUS_TIMEOUT = 45
DEGRADED_SCORE   = 50


class ConsensusEngine:
    def __init__(self, provider):
        self.market  = MarketAgent(provider)
        self.risk    = RiskAgent(provider)
        self.psych   = PsychologyAgent(provider)
        self.judge   = JudgeAgent(provider)
        self.context_builder      = ContextBuilder()
        self.scorer               = TradeScorer()
        self.deterministic_scorer = ScoringEngine()

    async def analyze_open_position(self, position: dict) -> dict:
        raw_position = position
        position = normalize_position(position)
        # position_plan (Position Analyst / Trade Management, см.
        # DECISION_FLOW_AUDIT.md, Вариант C) теперь строится здесь, а не
        # отдельным путём в AIOrchestrator.review_open_position() — чтобы
        # его сигналы дошли до JudgeAgent (override и/или структурный
        # компонент скора), а не были вторым независимым вердиктом.
        context, position_plan = await asyncio.gather(
            self.context_builder.build_for_open_position(position),
            build_structure_plan(raw_position),
        )
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны.")
        context['position_plan'] = position_plan
        logger.info(f"CONSENSUS ENGINE: analyzing position {position.get('symbol')}")
        return await self._run_agents_parallel(context, 'open')

    async def analyze_new_setup(self, ticker: str, direction: str, extra_notes: str = '') -> dict:
        context = await self.context_builder.build_for_new_setup(ticker, direction, extra_notes)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны.")
        logger.info(f"CONSENSUS ENGINE: analyzing setup {ticker} {direction}")
        return await self._run_agents_parallel(context, 'setup')

    async def analyze_closed_trade(self, trade: dict) -> dict:
        trade   = normalize_trade(trade)
        context = await self.context_builder.build_for_closed_trade(trade, None)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны.")
        # Реальный balance/losing_streak из уже собранного контекста — раньше
        # TradeScorer.score(trade) вызывался вообще без context (баланс
        # неоткуда было взять до сборки контекста), поэтому компонент
        # risk_per_trade всегда уходил в нейтральный дефолт (см.
        # TRADER_DNA_V1.md §1.1, DNA v2).
        context['score'] = self.scorer.score(trade, context={
            'balance': (context.get('portfolio') or {}).get('balance') or 0,
            'losing_streak': (context.get('history') or {}).get('losing_streak', 0),
        })
        logger.info(f"CONSENSUS ENGINE: analyzing closed trade {trade.get('symbol')}")
        return await self._run_agents_parallel(context, 'post_trade')

    async def _run_agents_parallel(self, context: dict, mode: str) -> dict:
        context['mode'] = mode
        degraded        = False
        degraded_agents = []

        async def _run_agent(name: str, agent, ctx: dict):
            try:
                result = await asyncio.wait_for(agent.analyze(ctx), timeout=AGENT_TIMEOUT)
                return self._parse_agent_response(result, name)
            except asyncio.TimeoutError:
                logger.warning(f"{name} timed out")
                return self._degraded_result(name, "timeout")
            except Exception as e:
                logger.error(f"{name} failed: {e}")
                return self._degraded_result(name, str(e))

        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    _run_agent("MarketAgent",      self.market, context),
                    _run_agent("RiskAgent",        self.risk,   context),
                    _run_agent("PsychologyAgent",  self.psych,  context),
                ),
                timeout=CONSENSUS_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.error("Consensus timed out")
            return self._error_response("Анализ превысил допустимое время.")

        market_result, risk_result, psych_result = results

        for res in [market_result, risk_result, psych_result]:
            if res.get('degraded'):
                degraded = True
                degraded_agents.append(res.get('agent_name', 'unknown'))

        market_text = market_result.get('text', str(market_result))
        risk_text   = risk_result.get('text', str(risk_result))
        psych_text  = psych_result.get('text', str(psych_result))

        market_trend = self._extract_market_trend(market_text, context)

        target_obj = context.get('position') or context.get('trade') or {}
        det_scores = self.deterministic_scorer.calculate(target_obj, mode, market_regime=market_trend)

        risk_score  = det_scores['risk_score']
        psych_score = det_scores['psychology_score']
        market_score = market_result.get('score', DEGRADED_SCORE)

        # Реальный disagreement на основе фактических оценок агентов
        disagreement = self._calc_real_disagreement(market_score, risk_score, psych_score)

        # Обновляем confidence с учётом реального disagreement
        confidence = det_scores['confidence']
        if disagreement > 0.3:
            confidence = max(0.15, confidence - (disagreement - 0.3) * 0.5)
        confidence = round(confidence, 2)

        trade_score = None
        position    = context.get('position')
        trade       = context.get('trade')
        if position:
            try:
                trade_score = self.scorer.score_open_position(position).get('total_score', 5) * 10
            except Exception:
                pass
        elif trade:
            try:
                # context['score'] уже посчитан с реальным balance в
                # analyze_closed_trade() — переиспользуем вместо слепого
                # пересчёта без context.
                score_result = context.get('score') or self.scorer.score(trade)
                trade_score = score_result.get('total_score', 5) * 10
            except Exception:
                pass

        try:
            verdict = await asyncio.wait_for(
                self.judge.synthesize(
                    json.dumps(market_result.get('raw', {})),
                    json.dumps({"risk_score": risk_score, "summary": risk_text}),
                    json.dumps({"psychology_score": psych_score, "summary": psych_text}),
                    mode=mode,
                    trade_score=trade_score,
                    confidence=confidence,
                    disagreement=disagreement,
                    trader_context=context.get('trader_context'),
                    position_plan=context.get('position_plan'),
                ),
                timeout=AGENT_TIMEOUT
            )
        except Exception as e:
            verdict = json.dumps({
                "final_score": 0,
                "verdict": "AVOID",
                "summary": f"Ошибка JudgeAgent: {e}"
            })
            degraded = True
            degraded_agents.append("JudgeAgent")

        data_quality = det_scores['data_quality']
        memory = context.get('memory', '')
        if degraded:
            memory = f"⚠️ Анализ неполный. Отказали: {', '.join(degraded_agents)}.\n" + memory

        return {
            'market_review':    market_text,
            'risk_review':      risk_text,
            'psychology_review': psych_text,
            'market_trend':     market_trend,
            'judge_verdict':    verdict,
            'confidence':       confidence,
            'disagreement':     disagreement,
            'data_quality':     data_quality,
            'degraded':         degraded,
            'memory':           memory,
            'score_breakdown':  context.get('score'),
            'position_plan':    context.get('position_plan'),
        }

    # ─── helpers ────────────────────────────────────────────────

    @staticmethod
    def _calc_real_disagreement(market_score: float, risk_score: float, psych_score: float) -> float:
        """Реальное расхождение агентов через стандартное отклонение."""
        scores = [market_score, risk_score, psych_score]
        mean   = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        stdev  = math.sqrt(variance)
        return round(min(1.0, stdev / 100), 2)

    def _extract_market_trend(self, market_text: str, context: dict) -> str:
        text_lower = market_text.lower()
        if any(w in text_lower for w in ['bullish', 'бычий', 'восходящий', 'рост']):
            return "BULLISH"
        if any(w in text_lower for w in ['bearish', 'медвежий', 'нисходящий', 'падение']):
            return "BEARISH"
        if any(w in text_lower for w in ['sideways', 'нейтральный', 'боковик', 'консолидация']):
            return "SIDEWAYS"
        if any(w in text_lower for w in ['ranging', 'рейнджинг', 'диапазон', 'флэт']):
            return "RANGING"
        market = context.get('market') or {}
        trend  = market.get('trend', '')
        if trend in ('BULLISH', 'BEARISH', 'SIDEWAYS', 'RANGING'):
            return trend
        return "UNKNOWN"

    def _parse_agent_response(self, response: str, agent_name: str) -> dict:
        try:
            data = json.loads(response) if isinstance(response, str) else response
        except (json.JSONDecodeError, TypeError):
            return {
                'text':       str(response),
                'score':      DEGRADED_SCORE,
                'confidence': 0.3,
                'degraded':   True,
                'agent_name': agent_name,
                'raw':        {}
            }
        text  = data.get('analysis') or data.get('summary') or str(data)
        score = (
            data.get('market_score') or
            data.get('risk_score')   or
            data.get('psychology_score') or
            DEGRADED_SCORE
        )
        confidence = data.get('confidence', 0.5)
        return {
            'text':       str(text),
            'score':      int(score) if score else DEGRADED_SCORE,
            'confidence': float(confidence),
            'degraded':   False,
            'agent_name': agent_name,
            'raw':        data
        }

    def _degraded_result(self, agent_name: str, error: str) -> dict:
        return {
            'text':       f"⚠️ {agent_name} недоступен: {error}",
            'score':      DEGRADED_SCORE,
            'confidence': 0.0,
            'degraded':   True,
            'agent_name': agent_name,
            'raw':        {}
        }

    def _is_market_data_valid(self, context: dict) -> bool:
        if context.get('idea'):
            ticker = context.get('ticker') or {}
            if ticker.get('price', 0) > 0:
                return True
            market = context.get('market') or {}
            btc    = market.get('btc') or {}
            return btc.get('price', 0) > 0
        market = context.get('market') or context.get('market_snapshot', {}) or {}
        btc    = market.get('btc') or {}
        eth    = market.get('eth') or {}
        if btc.get('price', 0) > 0:
            return True
        if eth.get('price', 0) > 0:
            return True
        return False

    def _error_response(self, message: str) -> dict:
        return {
            'market_review':     message,
            'risk_review':       message,
            'psychology_review': message,
            'market_trend':      'UNKNOWN',
            'judge_verdict':     json.dumps({
                "verdict": "AVOID", "final_score": 0, "summary": message
            }),
            'confidence':    0.0,
            'disagreement':  0.0,
            'data_quality':  0.0,
            'degraded':      True,
            'memory':        ''
        }