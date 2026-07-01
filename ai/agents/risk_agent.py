import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder
from ai.risk_engine import RiskRuleEngine

logger = logging.getLogger(__name__)

class RiskAgent:
    """Агент оценки риска: детерминированный анализ позиций/сделок, rule-based для портфеля."""

    def __init__(self, provider: BaseProvider = None):
        self.provider = provider          # больше не используется для позиций/сделок
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        if context is None:
            ctx = await self.context_builder.build_full_context()
        else:
            ctx = context

        mode = ctx.get('mode', 'open')
        trade = ctx.get('trade')
        position = ctx.get('position')

        if mode == 'post_trade' and trade:
            return self._analyze_post_trade(trade)
        if mode == 'open' and position:
            return self._analyze_open_position(position)
        return await self._rule_based_analysis(ctx)

    # ── Детерминированный анализ закрытой сделки ─────────────────
    def _analyze_post_trade(self, trade: dict) -> str:
        entry = float(trade.get('entry_price', 0))
        exit_p = float(trade.get('exit_price', 0))
        pnl = float(trade.get('realized_pnl', 0))
        sl = trade.get('stop_loss')
        tp = trade.get('take_profit')
        duration = trade.get('holding_minutes', '?')

        # Расчёт RR и процентов
        sl_pct = f"{abs(entry - sl) / entry * 100:.2f}%" if sl and entry else "—"
        tp_pct = f"{abs(tp - entry) / entry * 100:.2f}%" if tp and entry else "—"
        if sl and tp and entry:
            reward = abs(tp - entry)
            risk = abs(entry - sl)
            rr = f"1:{reward / risk:.2f}" if risk > 0 else "—"
        else:
            rr = "—"

        # Оценка исполнения
        if pnl > 0 and tp and exit_p >= tp:
            execution = "Тейк-профит достигнут, сделка исполнена по плану."
        elif pnl < 0 and sl and exit_p <= sl:
            execution = "Стоп-лосс сработал, потери ограничены."
        else:
            execution = "Исполнение без явного TP/SL."

        summary = (
            f"Стоп-лосс: {sl_pct} от входа, тейк-профит: {tp_pct}, RR = {rr}. "
            f"{execution} "
            f"Длительность: {duration} мин. PnL: ${pnl:+.2f}."
        )
        result = {
            "risk_score": self._calc_risk_score(trade),
            "summary": summary
        }
        return json.dumps(result, ensure_ascii=False)

    # ── Детерминированный анализ открытой позиции ─────────────────
    def _analyze_open_position(self, pos: dict) -> str:
        entry = float(pos.get('entry_price', 0))
        sl = pos.get('stop_loss')
        tp = pos.get('take_profit')
        leverage = pos.get('leverage', 1)
        size = pos.get('size', 0)
        current = pos.get('current_price', entry) or entry
        pnl = float(pos.get('unrealized_pnl', 0))

        # Проценты и RR
        sl_pct = f"{abs(entry - sl) / entry * 100:.2f}%" if sl and entry else "—"
        tp_pct = f"{abs(tp - entry) / entry * 100:.2f}%" if tp and entry else "—"
        if sl and tp and entry:
            reward = abs(tp - entry)
            risk = abs(entry - sl)
            rr = f"1:{reward / risk:.2f}" if risk > 0 else "—"
        else:
            rr = "—"

        # Потенциальный убыток при SL (без плеча)
        if sl and size and entry:
            loss_if_sl = abs(entry - sl) * size   # ← исправлено
            loss_str = f"${loss_if_sl:.2f}"
        else:
            loss_str = "неизвестен"

        # Оценка адекватности SL/TP
        if sl and tp:
            adequacy = "Защитные ордера установлены, риск контролируется."
        elif sl and not tp:
            adequacy = "Стоп-лосс установлен, но отсутствует тейк-профит."
        elif tp and not sl:
            adequacy = "Тейк-профит установлен, но стоп-лосс отсутствует – высокий риск."
        else:
            adequacy = "Стоп-лосс и тейк-профит не установлены – неконтролируемый риск."

        summary = (
            f"SL: {sl_pct} от входа, TP: {tp_pct}, RR = {rr}. "
            f"Потенциальный убыток при SL: {loss_str}. "
            f"{adequacy} "
            f"Плечо: {leverage}x, размер позиции: {size}."
        )
        result = {
            "risk_score": self._calc_risk_score(pos),
            "summary": summary
        }
        return json.dumps(result, ensure_ascii=False)

    def _calc_risk_score(self, obj: dict) -> int:
        """Простой детерминированный скор (0-10), синхронизированный со ScoringEngine."""
        score = 5
        if not obj.get('stop_loss'):
            score -= 2
        if not obj.get('take_profit'):
            score -= 1
        leverage = float(obj.get('leverage', 1))
        if leverage >= 10:
            score -= 2
        elif leverage >= 5:
            score -= 1
        return max(0, min(10, score))

    # ── Rule-based портфельный анализ (без изменений) ────────────
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