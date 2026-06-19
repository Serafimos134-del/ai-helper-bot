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
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны. Попробуйте позже.")
        logger.info(f"CONSENSUS ENGINE: analyzing position {position.get('symbol')}")
        return await self._run_agents(context, 'open')

    async def analyze_new_setup(self, ticker: str, direction: str, extra_notes: str = '') -> dict:
        context = await self.context_builder.build_for_new_setup(ticker, direction, extra_notes)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны. Попробуйте позже.")
        logger.info(f"CONSENSUS ENGINE: analyzing setup {ticker} {direction}")
        return await self._run_agents(context, 'setup')

    async def analyze_closed_trade(self, trade: dict) -> dict:
        score_result = self.scorer.score(trade)
        context = await self.context_builder.build_for_closed_trade(trade, score_result)
        if not self._is_market_data_valid(context):
            return self._error_response("Данные рынка недоступны. Попробуйте позже.")
        logger.info(f"CONSENSUS ENGINE: analyzing closed trade {trade.get('symbol')}")
        return await self._run_agents(context, 'post_trade')

    async def _run_agents(self, context: dict, mode: str) -> dict:
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

        # Оценка качества данных и согласованности агентов
        data_quality = self._calculate_data_quality(context)
        disagreement = self._calculate_disagreement(market, risk, psych)
        confidence = self._calculate_confidence(data_quality, disagreement)

        verdict = await asyncio.wait_for(self.judge.synthesize(market, risk, psych, mode=mode), timeout=AGENT_TIMEOUT)

        return {
            'market_review': str(market),
            'risk_review': str(risk),
            'psychology_review': str(psych),
            'judge_verdict': verdict,
            'confidence': round(confidence, 2),
            'disagreement': round(disagreement, 2),
            'data_quality': round(data_quality, 2)
        }

    def _calculate_data_quality(self, context: dict) -> float:
        """
        Оценивает качество входных данных от 0 до 1.
        """
        score = 0.0
        # Есть ли рыночные данные по инструменту?
        ticker = context.get('ticker')
        if ticker and ticker.get('price', 0) > 0:
            score += 0.4
        # Есть ли BTC данные?
        market = context.get('market', {})
        btc = market.get('btc', {}) if market else {}
        if btc and btc.get('price', 0) > 0:
            score += 0.3
        # Есть ли история сделок?
        history = context.get('history', {})
        if history and history.get('stats', {}).get('total_trades', 0) > 0:
            score += 0.2
        # Есть ли данные портфеля?
        portfolio = context.get('portfolio', {})
        if portfolio and portfolio.get('balance', 0) > 0:
            score += 0.1
        return min(1.0, score)

    def _calculate_disagreement(self, market: str, risk: str, psych: str) -> float:
        """
        Оценивает степень разногласий между агентами (0 = согласие, 1 = полное несогласие).
        Упрощённо: по наличию ключевых слов-противоречий.
        """
        disagreement_score = 0.0
        market_lower = market.lower()
        risk_lower = risk.lower()
        psych_lower = psych.lower()

        # Market vs Risk: BUY vs HIGH risk = разногласие
        if ('buy' in market_lower or 'bullish' in market_lower) and ('high' in risk_lower or 'extreme' in risk_lower):
            disagreement_score += 0.4
        # Market vs Psych: BUY vs EMOTIONAL/REVENGE = разногласие
        if ('buy' in market_lower) and ('revenge' in psych_lower or 'tilt' in psych_lower or 'emotional' in psych_lower):
            disagreement_score += 0.3
        # Risk vs Psych: SAFE vs REVENGE = разногласие
        if ('safe' in risk_lower) and ('revenge' in psych_lower or 'tilt' in psych_lower):
            disagreement_score += 0.2
        # Если агенты явно противоречат (WAIT vs ENTER)
        if ('wait' in market_lower or 'wait' in risk_lower) and ('входить' in psych_lower.lower() or 'buy' in psych_lower):
            disagreement_score += 0.3

        return min(1.0, disagreement_score)

    def _calculate_confidence(self, data_quality: float, disagreement: float) -> float:
        """
        Итоговая уверенность на основе качества данных и согласованности агентов.
        """
        # Чем выше качество данных и ниже разногласия, тем выше уверенность
        confidence = data_quality * (1.0 - disagreement * 0.5)
        return max(0.0, min(1.0, confidence))

    def _is_market_data_valid(self, context: dict) -> bool:
        """Проверяем, что есть рыночные данные, соответствующие запросу."""
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
            'data_quality': 0.0
        }