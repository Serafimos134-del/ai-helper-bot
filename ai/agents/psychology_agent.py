import asyncio
import logging
import json
from ai.context_builder import ContextBuilder
from ai.psychology_engine import PsychologyEngine

logger = logging.getLogger(__name__)

class PsychologyAgent:
    """Агент анализа психологии: LLM-анализ для сделок/позиций, rule-based для портфеля."""

    def __init__(self, provider=None):
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

        # Во всех остальных случаях — rule-based анализ портфеля
        return await self._rule_based_analysis(ctx)

    async def _analyze_post_trade(self, trade: dict) -> str:
        prompt = f"""You are a Trading Psychology Analyst. Analyze behavior for this closed trade:
Symbol: {trade.get('symbol')}
Side: {trade.get('side')}
Entry: {trade.get('entry_price')}, Exit: {trade.get('exit_price')}
PnL: {trade.get('realized_pnl')}
Stop Loss: {trade.get('stop_loss')}, Take Profit: {trade.get('take_profit')}
Duration: {trade.get('holding_minutes', '?')} min
Comment: {trade.get('exit_comment', '')}

Infer behavioral patterns: discipline (adherence to SL/TP), emotional signals (early exit, greed, fear).
Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"psychology_score": <0-10>, "summary": "<your analysis>"}}
"""
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(None, self.provider.generate, prompt)
            try:
                start = resp.find('{')
                end = resp.rfind('}') + 1
                if start >= 0 and end > start:
                    parsed = json.loads(resp[start:end])
                    if 'psychology_score' not in parsed:
                        parsed['psychology_score'] = 5
                    if 'summary' not in parsed:
                        parsed['summary'] = resp
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
            return json.dumps({"psychology_score": 5, "summary": resp}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"PsychologyAgent post_trade error: {e}")
            return json.dumps({"psychology_score": 0, "summary": f"Анализ психологии недоступен: {e}"}, ensure_ascii=False)

    async def _analyze_open_position(self, pos: dict) -> str:
        sl = pos.get('stop_loss')
        tp = pos.get('take_profit')
        pnl = pos.get('unrealized_pnl', 0)
        prompt = f"""You are a Trading Psychology Analyst evaluating an OPEN position.
        
POSITION:
Symbol: {pos.get('symbol')} {pos.get('side')}
Entry: {pos.get('entry_price')}, Unrealized PnL: {pnl}
Stop Loss: {sl if sl else "не установлен"}
Take Profit: {tp if tp else "не установлен"}

Analyze the trader's psychological state from the position management perspective:
- Is the trader disciplined (SL/TP set)?
- Is there greed (no TP) or fear (too tight SL)?
- What emotional patterns can be inferred from this position setup?

Answer in Russian, 2-3 sentences. Return ONLY valid JSON:
{{"psychology_score": <0-10>, "summary": "<your analysis>"}}
"""
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(None, self.provider.generate, prompt)
            try:
                start = resp.find('{')
                end = resp.rfind('}') + 1
                if start >= 0 and end > start:
                    parsed = json.loads(resp[start:end])
                    if 'psychology_score' not in parsed:
                        parsed['psychology_score'] = 5
                    if 'summary' not in parsed:
                        parsed['summary'] = resp
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
            return json.dumps({"psychology_score": 5, "summary": resp}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"PsychologyAgent open_position error: {e}")
            return json.dumps({"psychology_score": 0, "summary": f"Анализ психологии недоступен: {e}"}, ensure_ascii=False)

    async def _rule_based_analysis(self, ctx: dict) -> str:
        history = ctx.get("history", {})
        signals = PsychologyEngine.assess(history)
        signals["summary"] = self._build_template_summary(signals)
        return json.dumps(signals, ensure_ascii=False, indent=2)

    @staticmethod
    def _build_template_summary(signals: dict) -> str:
        flags = signals.get("flags", [])
        score = signals.get("psychology_score", 50)
        if not flags:
            return "Психологическое состояние стабильное. Отклонений не обнаружено."
        messages = []
        flag_map = {
            "overtrading": "Обнаружен риск овертрейдинга. Снизить частоту входов.",
            "revenge_trading": "Признаки revenge trading. Рекомендуется пауза минимум 24 часа.",
            "tilt": "Высокая вероятность тильта. Сделать перерыв.",
            "fomo": "Замечен FOMO-паттерн. Пересмотреть критерии входа.",
            "high_stress": "Повышенный стресс. Новые сделки не рекомендуются.",
        }
        for flag in flags:
            if flag in flag_map:
                messages.append(flag_map[flag])
            else:
                messages.append(f"Обнаружен флаг: {flag}.")
        if score < 40:
            messages.append("Психологический счёт критически низкий. Настоятельно рекомендуется пауза.")
        elif score < 60:
            messages.append("Психологический счёт ниже нормы. Требуется осознанный контроль эмоций.")
        return " | ".join(messages)