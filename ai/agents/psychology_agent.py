import asyncio
import logging
import json
from ai.context_builder import ContextBuilder
from ai.psychology_engine import PsychologyEngine

logger = logging.getLogger(__name__)

class PsychologyAgent:
    """Агент психологии: детерминированный анализ позиций/сделок/сетапов, rule-based для портфеля."""

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
        idea = ctx.get('idea') or {}

        if mode == 'post_trade' and trade:
            return self._analyze_post_trade(trade)
        if mode == 'open' and position:
            return self._analyze_open_position(position)
        # --- новый блок для сетапов ---
        if mode == 'setup' and idea:
            return self._analyze_setup(idea)
        # --- конец нового блока ---
        return await self._rule_based_analysis(ctx)

    # ── Детерминированный анализ сетапа ──────────────────────────
    def _analyze_setup(self, idea: dict) -> str:
        notes = idea.get('notes', '')
        direction = idea.get('direction', '')
        symbol = idea.get('symbol', '')

        patterns = []
        discipline_score = 8   # базовый уровень для сетапа

        if notes:
            if 'думаю' in notes.lower():
                patterns.append("Неуверенность в формулировке — возможно, недостаток уверенности в сетапе.")
                discipline_score -= 1
            if 'хочу' in notes.lower():
                patterns.append("Эмоциональное желание вместо объективного анализа — риск FOMO.")
                discipline_score -= 2
            if 'должен' in notes.lower():
                patterns.append("Чувство обязательства — возможное давление или revenge trading.")
                discipline_score -= 2

        if direction:
            if direction.upper() == 'LONG':
                patterns.append("Направление LONG — проверьте, нет ли перекоса в сторону лонгов.")
            elif direction.upper() == 'SHORT':
                patterns.append("Направление SHORT — проверьте, нет ли избыточной уверенности в падении.")

        if not notes:
            patterns.append("Сетап сформулирован без деталей — рекомендуется добавить больше конкретики.")
            discipline_score -= 1

        discipline_score = max(0, min(10, discipline_score))

        result = {
            "psychology_score": discipline_score,
            "summary": " ".join(patterns) if patterns else "Формальных признаков эмоциональных ошибок нет."
        }
        return json.dumps(result, ensure_ascii=False)

    # ── Существующие методы (без изменений) ──────────────────────
    def _analyze_post_trade(self, trade: dict) -> str:
        sl = trade.get('stop_loss')
        tp = trade.get('take_profit')
        exit_price = trade.get('exit_price', 0)
        pnl = trade.get('realized_pnl', 0)
        duration = trade.get('holding_minutes', '?')
        comment = trade.get('exit_comment', '')

        sl_hit = sl and exit_price <= sl and pnl < 0
        tp_hit = tp and exit_price >= tp and pnl > 0
        early_exit = not sl_hit and not tp_hit and duration != '?' and int(duration or 0) < 60

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
        if not sl: discipline_score -= 3
        if not tp: discipline_score -= 2
        if early_exit: discipline_score -= 2
        discipline_score = max(0, min(10, discipline_score))

        result = {
            "psychology_score": discipline_score,
            "summary": " ".join(patterns)
        }
        return json.dumps(result, ensure_ascii=False)

    def _analyze_open_position(self, pos: dict) -> str:
        sl = pos.get('stop_loss')
        tp = pos.get('take_profit')
        leverage = pos.get('leverage', 1)
        size = pos.get('size', 0)

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

        if size > 0:
            patterns.append(f"Размер позиции: {size}.")

        discipline_score = max(0, min(10, discipline_score))

        result = {
            "psychology_score": discipline_score,
            "summary": " ".join(patterns)
        }
        return json.dumps(result, ensure_ascii=False)

    # ── Rule-based портфельный анализ (без изменений) ────────────
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