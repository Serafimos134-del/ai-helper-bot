import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder
from ai.risk_engine import RiskRuleEngine
from ai.engines.scoring_engine import ScoringEngine
from ai.engines.structure_arbiter import format_sl_tp_block

logger = logging.getLogger(__name__)

class RiskAgent:
    """Агент оценки риска: детерминированный анализ позиций/сделок/сетапов, rule-based для портфеля."""

    def __init__(self, provider: BaseProvider = None):
        self.provider = provider
        self.context_builder = ContextBuilder()
        # Единый источник правды для risk_score (шкала 0-100, выше = безопаснее) —
        # тот же ScoringEngine, который видит JudgeAgent. Раньше здесь была
        # отдельная копия той же логики в шкале 0-10 (см. AUDIT.md).
        self.scoring = ScoringEngine()

    async def analyze(self, context: dict = None) -> str:
        if context is None:
            ctx = await self.context_builder.build_full_context()
        else:
            ctx = context

        mode = ctx.get('mode', 'open')
        trade = ctx.get('trade')
        position = ctx.get('position')
        idea = ctx.get('idea') or {}
        position_plan = ctx.get('position_plan')

        if mode == 'post_trade' and trade:
            return self._analyze_post_trade(trade)
        if mode == 'open' and position:
            return self._analyze_open_position(position, position_plan)
        # --- новый блок для сетапов ---
        if mode == 'setup' and idea:
            return self._analyze_setup(idea, ctx.get('ticker', {}))
        # --- конец нового блока ---
        return await self._rule_based_analysis(ctx)

    # ── Детерминированный анализ сетапа ──────────────────────────
    def _analyze_setup(self, idea: dict, ticker: dict) -> str:
        direction = idea.get('direction', '')
        symbol = idea.get('symbol', '')
        price = ticker.get('price', 0) or 0
        atr = ticker.get('atr', 0) or 0
        funding = ticker.get('funding_rate', 0) or 0
        regime = ticker.get('market_regime', 'UNKNOWN')

        # Оценка волатильности
        if atr and price:
            atr_pct = f"{atr / price * 100:.2f}%"
        else:
            atr_pct = "—"

        # Оценка funding
        if funding:
            funding_note = "перегрев лонгов" if funding > 0.01 else "перегрев шортов" if funding < -0.01 else "нейтральный"
        else:
            funding_note = "неизвестно"

        regime_risk = {
            "TRENDING_UP": "низкий (тренд вверх)",
            "TRENDING_DOWN": "высокий (тренд вниз)",
            "RANGING": "умеренный (боковик)",
            "SIDEWAYS": "умеренный (боковик)",
            "UNKNOWN": "высокий (неопределённый рынок)"
        }.get(regime, "неизвестен")

        summary = (
            f"Сетап {symbol} {direction}. "
            f"Цена: ${price:.4f}, ATR: {atr_pct}, Funding: {funding_note}. "
            f"Рыночный риск: {regime_risk}. "
            f"Рекомендуется ограничить размер позиции 1% от депозита."
        )

        # Скор безопасности сетапа, шкала 0-100 (выше = безопаснее) — та же
        # ориентация, что и ScoringEngine.calc_risk для open/post_trade и
        # JudgeAgent. Раньше здесь была отдельная шкала 0-10 с обратной
        # ориентацией (выше = опаснее); значения ниже — те же самые числа
        # (5→50, 3→70, 7→30, 8→20), только приведённые к общей конвенции.
        risk_score = 50
        if regime in ("TRENDING_UP", "TRENDING_DOWN"):
            risk_score = 70 if direction.lower() == "long" and regime == "TRENDING_UP" else 30
        elif regime in ("RANGING", "SIDEWAYS"):
            risk_score = 50
        elif regime == "UNKNOWN":
            risk_score = 20

        result = {
            "risk_score": risk_score,
            "summary": summary
        }
        return json.dumps(result, ensure_ascii=False)

    # ── Существующие методы (без изменений) ──────────────────────
    def _analyze_post_trade(self, trade: dict) -> str:
        entry = float(trade.get('entry_price', 0))
        exit_p = float(trade.get('exit_price', 0))
        pnl = float(trade.get('realized_pnl', 0))
        sl = trade.get('stop_loss')
        tp = trade.get('take_profit')
        duration = trade.get('holding_minutes', '?')

        sl_pct = f"{abs(entry - sl) / entry * 100:.2f}%" if sl and entry else "—"
        tp_pct = f"{abs(tp - entry) / entry * 100:.2f}%" if tp and entry else "—"
        if sl and tp and entry:
            reward = abs(tp - entry)
            risk = abs(entry - sl)
            rr = f"1:{reward / risk:.2f}" if risk > 0 else "—"
        else:
            rr = "—"

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

    def _analyze_open_position(self, pos: dict, position_plan: dict = None) -> str:
        entry = float(pos.get('entry_price', 0))
        sl = pos.get('stop_loss')
        tp = pos.get('take_profit')
        leverage = pos.get('leverage', 1)
        size = pos.get('size', 0)
        current = pos.get('current_price', entry) or entry
        pnl = float(pos.get('unrealized_pnl', 0))

        sl_pct = f"{abs(entry - sl) / entry * 100:.2f}%" if sl and entry else "—"
        tp_pct = f"{abs(tp - entry) / entry * 100:.2f}%" if tp and entry else "—"
        if sl and tp and entry:
            reward = abs(tp - entry)
            risk = abs(entry - sl)
            rr = f"1:{reward / risk:.2f}" if risk > 0 else "—"
        else:
            rr = "—"

        if sl and size and entry:
            loss_if_sl = abs(entry - sl) * size
            loss_str = f"${loss_if_sl:.2f}"
        else:
            loss_str = "неизвестен"

        # Recommended SL — расчётный уровень AI Core (structure_engine), не
        # факт того, что на бирже стоит защитный ордер. Используется только
        # чтобы честно объяснить пользователю разницу между "риск не
        # контролируется вовсе" и "риск не контролируется на бирже, но
        # AI Core предложил уровень" — risk_score (см. _calc_risk_score
        # ниже) НЕ меняется, продолжает штрафовать отсутствие реального
        # SL/TP как раньше (см. аудит источников данных Risk Agent —
        # Recommended SL/TP не является фактом защиты позиции).
        recommended_sl = (position_plan or {}).get('details', {}).get('stop', {}).get('hard_sl')

        if sl and tp:
            adequacy = "Защитные ордера установлены, риск контролируется."
        elif sl and not tp:
            adequacy = "Стоп-лосс установлен, но отсутствует тейк-профит."
        elif tp and not sl:
            adequacy = "Тейк-профит установлен, но стоп-лосс отсутствует – высокий риск."
        elif recommended_sl:
            adequacy = (
                "Стоп-лосс и тейк-профит не установлены на бирже – неконтролируемый риск. "
                "AI Core рассчитал рекомендуемый уровень (см. ниже), но это не факт защиты позиции."
            )
        else:
            adequacy = "Стоп-лосс и тейк-профит не установлены – неконтролируемый риск."

        summary = (
            f"SL: {sl_pct} от входа, TP: {tp_pct}, RR = {rr}. "
            f"Потенциальный убыток при SL: {loss_str}. "
            f"{adequacy} "
            f"Плечо: {leverage}x, размер позиции: {size}.\n\n"
            f"{format_sl_tp_block(pos, position_plan)}"
        )
        result = {
            "risk_score": self._calc_risk_score(pos),
            "summary": summary
        }
        return json.dumps(result, ensure_ascii=False)

    def _calc_risk_score(self, obj: dict) -> int:
        """Шкала 0-100 (выше = безопаснее), делегирует в ScoringEngine.calc_risk —
        единственный источник этой логики в проекте (см. AUDIT.md). Раньше здесь
        была отдельная копия той же проверки (SL/TP/leverage) в шкале 0-10."""
        return int(self.scoring.calc_risk(obj))

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