import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder
from ai.risk_engine import RiskRuleEngine

logger = logging.getLogger(__name__)

class RiskAgent:
    """Агент оценки риска: LLM-анализ для отдельных позиций/сделок, rule-based для портфеля."""

    def __init__(self, provider: BaseProvider = None):
        self.provider = provider
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        if context is None:
            ctx = await self.context_builder.build_full_context()
        else:
            ctx = context

        mode = ctx.get('mode', 'open')
        trade = ctx.get('trade')
        position = ctx.get('position')

        # LLM-анализ для закрытых сделок
        if mode == 'post_trade' and trade and self.provider:
            return await self._analyze_post_trade(trade)

        # LLM-анализ для открытых позиций
        if mode == 'open' and position and self.provider:
            return await self._analyze_open_position(position)

        # Во всех остальных случаях — rule-based портфельный анализ
        return await self._rule_based_analysis(ctx)

    async def _analyze_post_trade(self, trade: dict) -> str:
        sl = trade.get('stop_loss')
        tp = trade.get('take_profit')
        entry = trade.get('entry_price', 0)
        exit_p = trade.get('exit_price', 0)
        pnl = trade.get('realized_pnl', 0)
        prompt = f"""You are a Risk Analyst for closed trade evaluation.
Analyze ONLY this trade:
Symbol: {trade.get('symbol')}
Side: {trade.get('side')}
Entry: {entry}, Exit: {exit_p}, PnL: {pnl}
Stop Loss: {sl}, Take Profit: {tp}
Duration: {trade.get('holding_minutes', '?')} min

Evaluate:
- SL placement quality
- R:R ratio based on entry/exit
- whether risk was respected
- execution efficiency of TP/SL

Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"risk_score": <0-10>, "summary": "<your analysis>"}}
"""
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(None, self.provider.generate, prompt)
            try:
                start = resp.find('{')
                end = resp.rfind('}') + 1
                if start >= 0 and end > start:
                    parsed = json.loads(resp[start:end])
                    if 'risk_score' not in parsed:
                        parsed['risk_score'] = 5
                    if 'summary' not in parsed:
                        parsed['summary'] = resp
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
            return json.dumps({"risk_score": 5, "summary": resp}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"RiskAgent post_trade error: {e}")
            return json.dumps({"risk_score": 0, "summary": f"Анализ риска недоступен: {e}"}, ensure_ascii=False)

    async def _analyze_open_position(self, pos: dict) -> str:
        sl = pos.get('stop_loss')
        tp = pos.get('take_profit')
        entry = pos.get('entry_price', 0)
        pnl = pos.get('unrealized_pnl', 0)
        leverage = pos.get('leverage', 1)
        prompt = f"""You are a Risk Analyst evaluating an OPEN position.

POSITION:
Symbol: {pos.get('symbol')} {pos.get('side')}
Entry: {entry}, Unrealized PnL: {pnl}
Stop Loss: {sl if sl else "не установлен"}
Take Profit: {tp if tp else "не установлен"}
Leverage: {leverage}x

Evaluate the risk of THIS SINGLE POSITION:
- Is the Stop Loss adequate? (distance from entry)
- Is the Take Profit realistic?
- What is the potential loss if SL is hit?
- Is the position size reasonable?

Do NOT evaluate the whole portfolio. Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"risk_score": <0-10>, "summary": "<your analysis>"}}
"""
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(None, self.provider.generate, prompt)
            try:
                start = resp.find('{')
                end = resp.rfind('}') + 1
                if start >= 0 and end > start:
                    parsed = json.loads(resp[start:end])
                    if 'risk_score' not in parsed:
                        parsed['risk_score'] = 5
                    if 'summary' not in parsed:
                        parsed['summary'] = resp
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
            return json.dumps({"risk_score": 5, "summary": resp}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"RiskAgent open_position error: {e}")
            return json.dumps({"risk_score": 0, "summary": f"Анализ риска недоступен: {e}"}, ensure_ascii=False)

    async def _rule_based_analysis(self, ctx: dict) -> str:
        portfolio = ctx.get("portfolio", {})
        history = ctx.get("history", {})
        signals = RiskRuleEngine.assess(portfolio, history)
        summary = self._build_template_summary(signals)
        result = {
            "risk_score": (10 - signals.get('risk_score', 5)) * 10,
            "signals": signals,
            "summary": summary
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    @staticmethod
    def _build_template_summary(signals: dict) -> str:
        risk_level = signals.get('risk_level', 'UNKNOWN')
        risk_score = signals.get('risk_score', 0)
        warnings = signals.get('warnings', [])
        recommendation = signals.get('recommendation', '')
        level_text = {'SAFE': 'низкий', 'MODERATE': 'умеренный', 'HIGH': 'высокий', 'EXTREME': 'критический'}
        level_str = level_text.get(risk_level, risk_level)
        main_warning = warnings[0] if warnings else 'критических проблем нет'
        rec_text = {'ALLOW': 'Можно продолжать текущую стратегию.',
                    'REDUCE': 'Рекомендуется снизить размер позиций.',
                    'CAUTION': 'Требуется осторожность при открытии новых позиций.',
                    'STOP': 'Рекомендуется прекратить торговлю до стабилизации.'}
        rec_str = rec_text.get(recommendation, recommendation)
        return (f"Уровень риска: {risk_level} ({risk_score}/10) — {level_str}. "
                f"Главная проблема: {main_warning}. "
                f"Рекомендация: {rec_str}")