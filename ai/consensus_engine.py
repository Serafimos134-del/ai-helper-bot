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

AGENT_TIMEOUT = 30
AGENT_DELAY = 3.0


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
        return await self._run_agents_sequential(context, 'open')

    async def analyze_new_setup(self, ticker: str, direction: str, extra_notes: str = '') -> dict:
        context = await self.context_builder.build_for_new_setup(ticker, direction, extra_notes)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны. Попробуйте позже.")
        logger.info(f"CONSENSUS ENGINE: analyzing setup {ticker} {direction}")
        return await self._run_agents_sequential(context, 'setup')

    async def analyze_closed_trade(self, trade: dict) -> dict:
        score_result = self.scorer.score(trade)
        context = await self.context_builder.build_for_closed_trade(trade, score_result)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны. Попробуйте позже.")
        logger.info(f"CONSENSUS ENGINE: analyzing closed trade {trade.get('symbol')}")
        return await self._run_agents_sequential(context, 'post_trade')

    async def _run_agents_sequential(self, context: dict, mode: str) -> dict:
        # 1. MarketAgent
        try:
            market = await asyncio.wait_for(self.market.analyze(context), timeout=AGENT_TIMEOUT)
        except Exception as e:
            market = f'{{"market_score": 50, "analysis": "Ошибка MarketAgent: {e}"}}'
        await asyncio.sleep(AGENT_DELAY)

        # 2. RiskAgent
        try:
            raw_risk = await asyncio.wait_for(self.risk.analyze(context), timeout=AGENT_TIMEOUT)
        except Exception as e:
            raw_risk = f'{{"risk_score": 50, "summary": "Ошибка RiskAgent: {e}"}}'
        await asyncio.sleep(AGENT_DELAY)

        # 3. PsychologyAgent
        try:
            psych = await asyncio.wait_for(self.psych.analyze(context), timeout=AGENT_TIMEOUT)
        except Exception as e:
            psych = f'{{"psychology_score": 50, "summary": "Ошибка PsychologyAgent: {e}"}}'
        await asyncio.sleep(AGENT_DELAY)

        # 4. Вычисляем trade_score через TradeScorer
        trade_score = None
        position = context.get('position')    # открытая позиция
        trade = context.get('trade')          # закрытая сделка
        if position:
            try:
                score_result = self.scorer.score_open_position(position)
                trade_score = score_result.get('total_score', 5) * 10  # 0-10 → 0-100
            except Exception as e:
                logger.warning(f"TradeScorer (open position) failed: {e}")
        elif trade:
            try:
                score_result = self.scorer.score(trade)
                trade_score = score_result.get('total_score', 5) * 10
            except Exception as e:
                logger.warning(f"TradeScorer (closed trade) failed: {e}")

        # 5. JudgeAgent
        try:
            verdict = await asyncio.wait_for(
                self.judge.synthesize(market, raw_risk, psych, mode=mode, trade_score=trade_score),
                timeout=AGENT_TIMEOUT
            )
        except Exception as e:
            verdict = f'{{"final_score": 0, "verdict": "AVOID", "summary": "Ошибка JudgeAgent: {e}"}}'

        # Извлекаем читаемые строки из JSON
        market_text = self._extract_text(market, 'analysis', market)
        risk_text = self._extract_text(raw_risk, 'summary', raw_risk)
        psych_text = self._extract_text(psych, 'summary', psych)

        data_quality = self._calculate_data_quality(context)
        disagreement = self._calculate_disagreement(market, raw_risk, psych)
        confidence = self._calculate_confidence(data_quality, disagreement)
        memory = context.get('memory', '')

        return {
            'market_review': market_text,
            'risk_review': risk_text,
            'psychology_review': psych_text,
            'judge_verdict': verdict,
            'confidence': round(confidence, 2),
            'disagreement': round(disagreement, 2),
            'data_quality': round(data_quality, 2),
            'memory': memory
        }

    @staticmethod
    def _extract_text(json_str: str, key: str, fallback: str) -> str:
        """Извлекает читаемый текст из JSON-ответа агента."""
        try:
            if json_str.startswith('{'):
                return json.loads(json_str).get(key, fallback)
        except Exception:
            pass
        return str(fallback)

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
        if ('wait' in market_lower or 'wait' in risk_lower) and ('входить' in psych_lower.lower() or 'buy' in psych_lower):
            disagreement_score += 0.3

        return min(1.0, disagreement_score)

    def _calculate_confidence(self, data_quality: float, disagreement: float) -> float:
        confidence = data_quality * (1.0 - disagreement * 0.5)
        return max(0.0, min(1.0, confidence))

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
            'judge_verdict': 'Невозможно дать заключение из-за ошибки получения данных.',
            'confidence': 0.0,
            'disagreement': 0.0,
            'data_quality': 0.0,
            'memory': ''
        }