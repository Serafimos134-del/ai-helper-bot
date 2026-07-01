import asyncio
import logging
import json
from ai.context_builder import ContextBuilder
from ai.psychology_engine import PsychologyEngine

logger = logging.getLogger(__name__)


class PsychologyAgent:
    """Агент психологии: детерминированный анализ позиций/сделок, rule-based для портфеля."""

    def __init__(self, provider=None):
        self.provider = provider
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        if context is None:
            ctx = await self.context_builder.build_full_context()
        else:
            ctx = context

        mode     = ctx.get('mode', 'open')
        trade    = ctx.get('trade')
        position = ctx.get('position')

        if mode == 'post_trade' and trade:
            return self._analyze_post_trade(trade)
        if mode == 'open' and position:
            return self._analyze_open_position(position)
        return await self._rule_based_analysis(ctx)

    def _analyze_post_trade(self, trade: dict) -> str:
        sl         = trade.get('stop_loss')
        tp         = trade.get('take_profit')
        side       = trade.get('side', 'LONG')
        exit_price = float(trade.get('exit_price', 0))
        pnl        = float(trade.get('realized_pnl', 0))
        duration   = trade.get('holding_minutes')
        comment    = trade.get('exit_comment', '')

        # Безопасное вычисление duration
        try:
            holding_int = int(duration) if duration is not None and str(duration) != '?' else None
        except (ValueError, TypeError):
            holding_int = None

        # Проверка срабатывания SL с учётом направления
        if sl is not None:
            sl_f = float(sl)
            if side == 'LONG':
                sl_hit = exit_price <= sl_f and pnl < 0
            else:
                sl_hit = exit_price >= sl_f and pnl < 0
        else:
            sl_hit = False

        # Проверка срабатывания TP с учётом направления
        if tp is not None:
            tp_f = float(tp)
            if side == 'LONG':
                tp_hit = exit_price >= tp_f and pnl > 0
            else:
                tp_hit = exit_price <= tp_f and pnl > 0
        else:
            tp_hit = False

        # Ранний выход: закрыт не по SL/TP и менее 60 минут
        early_exit = (
            not sl_hit and
            not tp_hit and
            holding_int is not None and
            holding_int < 60
        )

        patterns = []
        if sl_hit:
            patterns.append("Стоп-лосс сработал — дисциплина соблюдена, потери ограничены.")
        elif tp_hit:
            patterns.append("Тейк-профит достигнут — план выполнен, эмоциональная устойчивость.")
        elif early_exit:
            patterns.append("Возможен преждевременный выход — признаки нетерпения или страха.")
        elif pnl < 0 and not sl:
            patterns.append("Убыток без стоп-лосса — потенциальная жадность или надежда на разворот.")
        elif pnl > 0 and not tp:
            patterns.append("Прибыль без тейк-профита — возможно, сработала жадность или не было плана.")
        else:
            patterns.append("Сделка без явных признаков эмоциональных ошибок.")

        if comment:
            patterns.append(f"Комментарий трейдера: {comment}")

        discipline_score = 10
        if not sl:
            discipline_score -= 3
        if not tp:
            discipline_score -= 2
        if early_exit:
            discipline_score -= 2
        discipline_score = max(0, min(10, discipline_score))

        result = {
            "psychology_score": discipline_score,
            "summary": " ".join(patterns)
        }
        return json.dumps(result, ensure_ascii=False)

    def _analyze_open_position(self, pos: dict) -> str:
        sl       = pos.get('stop_loss')
        tp       = pos.get('take_profit')
        leverage = float(pos.get('leverage', 1))
        size     = pos.get('size', 0)

        patterns = []
        discipline_score = 10

        if sl and tp:
            patterns.append("Трейдер установил оба защитных ордера — высокий уровень дисциплины.")
        elif sl and not tp:
            patterns.append("Установлен только стоп-лосс — недостаток планирования прибыли.")
            discipline_score -= 2
        elif tp and not sl:
            patterns.append("Установлен только тейк-профит — отсутствие защиты от убытков, рискованный оптимизм.")
            discipline_score -= 3
        else:
            patterns.append("Нет ни стоп-лосса, ни тейк-профита — отсутствие дисциплины и плана.")
            discipline_score -= 5

        if leverage >= 10:
            patterns.append("Высокое плечо (10x+) указывает на склонность к риску или самоуверенность.")
            discipline_score -= 2
        elif leverage >= 5:
            patterns.append("Умеренное плечо — приемлемый уровень риска.")

        if size:
            patterns.append(f"Размер позиции: {size}.")

        discipline_score = max(0, min(10, discipline_score))

        result = {
            "psychology_score": discipline_score,
            "summary": " ".join(patterns)
        }
        return json.dumps(result, ensure_ascii=False)

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
        flag_map = {
            "overtrading":     "Обнаружен риск овертрейдинга. Снизить частоту входов.",
            "revenge_trading": "Признаки revenge trading. Рекомендуется пауза минимум 24 часа.",
            "tilt":            "Высокая вероятность тильта. Сделать перерыв.",
            "fomo":            "Замечен FOMO-паттерн. Пересмотреть критерии входа.",
            "high_stress":     "Повышенный стресс. Новые сделки не рекомендуются.",
        }
        messages = [flag_map.get(f, f"Обнаружен флаг: {f}.") for f in flags]
        if score < 40:
            messages.append("Психологический счёт критически низкий. Настоятельно рекомендуется пауза.")
        elif score < 60:
            messages.append("Психологический счёт ниже нормы. Требуется осознанный контроль эмоций.")
        return " | ".join(messages)
