"""
ai/consensus_engine.py
Refactored consensus engine with parallel agents, deterministic scoring,
normalizer integration, and mode‑aware dispatch.
"""

import asyncio
import json
import logging
from ai.agents.market_agent import MarketAgent
from ai.agents.risk_agent import RiskAgent
from ai.agents.psychology_agent import PsychologyAgent
from ai.agents.judge_agent import JudgeAgent
from ai.context_builder import ContextBuilder
from ai.trade_scorer import TradeScorer
from ai.engines.normalizer import normalize_position, normalize_trade
from ai.engines.scoring_engine import ScoringEngine

logger = logging.getLogger(__name__)

AGENT_TIMEOUT = 30
CONSENSUS_TIMEOUT = 45
DEGRADED_SCORE = 50


class ConsensusEngine:
    def __init__(self, provider):
        self.market = MarketAgent(provider)
        self.risk = RiskAgent(provider)
        self.psych = PsychologyAgent(provider)
        self.judge = JudgeAgent(provider)
        self.context_builder = ContextBuilder()
        self.scorer = TradeScorer()
        self.deterministic_scorer = ScoringEngine()          # новый слой

    async def analyze_open_position(self, position: dict) -> dict:
        position = normalize_position(position)
        context = await self.context_builder.build_for_open_position(position)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны.")
        logger.info(f"CONSENSUS ENGINE: analyzing position {position.get('symbol')}")
        return await self._run_agents_parallel(context, 'open')

    async def analyze_new_setup(self, ticker: str, direction: str, extra_notes: str = '') -> dict:
        context = await self.context_builder.build_for_new_setup(ticker, direction, extra_notes)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны.")
        logger.info(f"CONSENSUS ENGINE: analyzing setup {ticker} {direction}")
        return await self._run_agents_parallel(context, 'setup')

    async def analyze_closed_trade(self, trade: dict) -> dict:
        trade = normalize_trade(trade)
        score_result = self.scorer.score(trade)
        context = await self.context_builder.build_for_closed_trade(trade, score_result)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны.")
        logger.info(f"CONSENSUS ENGINE: analyzing closed trade {trade.get('symbol')}")
        return await self._run_agents_parallel(context, 'post_trade')

    async def _run_agents_parallel(self, context: dict, mode: str) -> dict:
        context['mode'] = mode
        degraded = False
        degraded_agents = []

        async def _run_agent(name: str, agent, ctx: dict):
            try:
                result = await asyncio.wait_for(agent.analyze(ctx), timeout=AGENT_TIMEOUT)
                parsed = self._parse_agent_response(result, name)
                return parsed
            except asyncio.TimeoutError:
                logger.warning(f"{name} timed out")
                return self._degraded_result(name, "timeout")
            except Exception as e:
                logger.error(f"{name} failed: {e}")
                return self._degraded_result(name, str(e))

        # Параллельный запуск MarketAgent, RiskAgent, PsychologyAgent (LLM для текста)
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
            logger.error("Consensus timed out")
            return self._error_response("Анализ превысил допустимое время.")

        market_result, risk_result, psych_result = results

        # Проверка degradation
        for res in [market_result, risk_result, psych_result]:
            if res.get('degraded'):
                degraded = True
                degraded_agents.append(res.get('agent_name', 'unknown'))

        # Текстовые выводы (от LLM)
        market_text = market_result.get('text', str(market_result))
        risk_text = risk_result.get('text', str(risk_result))
        psych_text = psych_result.get('text', str(psych_result))

        # Извлекаем объект для детерминированного скоринга
        target_obj = context.get('position') or context.get('trade')
        if target_obj is None:
            target_obj = {}

        # Детерминированные метрики (Python)
        det_scores = self.deterministic_scorer.calculate(target_obj, mode)

        # Используем детерминированные risk и psychology scores
        risk_score = det_scores['risk_score']
        psych_score = det_scores['psychology_score']

        # Market score остаётся от LLM (MarketAgent)
        market_score = market_result.get('score', DEGRADED_SCORE)

        # Trade score (если есть)
        trade_score = None
        position = context.get('position')
        trade = context.get('trade')
        if position:
            try:
                trade_score = self.scorer.score_open_position(position).get('total_score', 5) * 10
            except Exception:
                pass
        elif trade:
            try:
                trade_score = self.scorer.score(trade).get('total_score', 5) * 10
            except Exception:
                pass

        # JudgeAgent с детерминированными скорами
        try:
            verdict = await asyncio.wait_for(
                self.judge.synthesize(
                    json.dumps(market_result.get('raw', {})),
                    json.dumps({"risk_score": risk_score, "summary": risk_text}),
                    json.dumps({"psychology_score": psych_score, "summary": psych_text}),
                    mode=mode,
                    trade_score=trade_score
                ),
                timeout=AGENT_TIMEOUT
            )
        except Exception as e:
            verdict = json.dumps({"final_score": 0, "verdict": "AVOID", "summary": f"Ошибка JudgeAgent: {e}"})
            degraded = True
            degraded_agents.append("JudgeAgent")

        market_trend = self._extract_market_trend(market_text, context)

        # Берём метрики из детерминированного скорера
        confidence = det_scores['confidence']
        data_quality = det_scores['data_quality']
        disagreement = det_scores['disagreement']

        memory = context.get('memory', '')
        if degraded:
            memory = f"⚠️ Анализ неполный. Отказали: {', '.join(degraded_agents)}.\n" + memory

        return {
            'market_review': market_text,
            'risk_review': risk_text,
            'psychology_review': psych_text,
            'market_trend': market_trend,
            'judge_verdict': verdict,
            'confidence': confidence,
            'disagreement': disagreement,
            'data_quality': data_quality,
            'degraded': degraded,
            'memory': memory
        }

    # ─── helpers (без изменений) ─────────────────────────────────
    def _extract_market_trend(self, market_text: str, context: dict) -> str:
        text_lower = market_text.lower()
        if any(w in text_lower for w in ['bullish', 'бычий', 'восходящий', 'рост']):
            return "BULLISH"
        if any(w in text_lower for w in ['bearish', 'медвежий', 'нисходящий', 'падение']):
            return "BEARISH"
        if any(w in text_lower for w in ['sideways', 'нейтральный', 'боковик', 'консолидация']):
            return "SIDEWAYS"
        market = context.get('market', {}) or {}
        trend = market.get('trend', '')
        if trend in ('BULLISH', 'BEARISH', 'SIDEWAYS'):
            return trend
        return "UNKNOWN"

    def _parse_agent_response(self, response: str, agent_name: str) -> dict:
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
        text = data.get('analysis') or data.get('summary') or str(data)
        score = (
            data.get('market_score') or
            data.get('risk_score') or
            data.get('psychology_score') or
            DEGRADED_SCORE
        )
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
        return {
            'text': f"⚠️ {agent_name} недоступен: {error}",
            'score': DEGRADED_SCORE,
            'confidence': 0.0,
            'degraded': True,
            'agent_name': agent_name,
            'raw': {}
        }

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