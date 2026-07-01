"""
services/coach_engine.py
AI Coach Engine — переводит метрики Performance Engine в конкретные выводы.
Не просто цифры, а инсайты на языке трейдера.
"""

import json
import logging

logger = logging.getLogger(__name__)


class CoachEngine:
    """Принимает данные Performance Engine и генерирует персональный коучинг."""

    def __init__(self, provider, db):
        self.provider = provider
        self.db = db

    async def generate_coaching(self, user_id: str = 'default') -> str:
        from services.performance_engine import PerformanceEngine
        import asyncio

        engine = PerformanceEngine(self.db)
        report = engine.get_full_report(user_id)

        if report.get('empty'):
            return "Закрой хотя бы 5 сделок — тогда смогу дать реальный разбор."

        trades = self.db.get_closed_trades(limit=20, user_id=user_id)

        prompt = self._build_prompt(report, trades)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, self.provider.generate, prompt)
            return response
        except Exception as e:
            logger.error(f"CoachEngine error: {e}")
            return self._fallback_coaching(report)

    def _build_prompt(self, report: dict, trades: list) -> str:
        b    = report.get('basic', {})
        rr   = report.get('rr', {})
        s    = report.get('streak', {})
        sess = report.get('session', {})
        lev  = report.get('leverage', {})
        setup = report.get('setup', {})

        # Находим худшее и лучшее время
        worst_session = None
        best_session  = None
        for key, d in sess.items():
            if d['total'] >= 2:
                if worst_session is None or d['winrate'] < sess[worst_session]['winrate']:
                    worst_session = key
                if best_session is None or d['winrate'] > sess[best_session]['winrate']:
                    best_session = key

        # Топ убыточный символ
        worst_symbol = None
        for sym, d in setup.items():
            if d['pnl'] < 0 and d['total'] >= 2:
                if worst_symbol is None or d['pnl'] < setup[worst_symbol]['pnl']:
                    worst_symbol = sym

        context = {
            'total_trades':    b.get('total', 0),
            'winrate':         b.get('winrate', 0),
            'total_pnl':       b.get('total_pnl', 0),
            'avg_win':         b.get('avg_win', 0),
            'avg_loss':        b.get('avg_loss', 0),
            'profit_factor':   b.get('profit_factor', 0),
            'rr_ratio':        rr.get('rr_ratio', 0),
            'current_loss_streak': s.get('current_loss_streak', 0),
            'max_loss_streak':     s.get('max_loss_streak', 0),
            'avg_leverage':    lev.get('avg_leverage', 0),
            'worst_session':   sess.get(worst_session, {}).get('label') if worst_session else None,
            'worst_session_wr': sess.get(worst_session, {}).get('winrate') if worst_session else None,
            'best_session':    sess.get(best_session, {}).get('label') if best_session else None,
            'best_session_wr': sess.get(best_session, {}).get('winrate') if best_session else None,
            'worst_symbol':    worst_symbol,
            'worst_symbol_pnl': setup.get(worst_symbol, {}).get('pnl') if worst_symbol else None,
            'recent_comments': [t.get('entry_comment') or t.get('exit_comment') for t in trades[:5] if t.get('entry_comment') or t.get('exit_comment')]
        }

        prompt = f"""Ты — строгий трейдер-коуч. Говоришь прямо, без воды, по делу.

ДАННЫЕ ТРЕЙДЕРА:
{json.dumps(context, ensure_ascii=False, indent=2)}

ЗАДАЧА:
Дай персональный разбор строго по этой структуре. Каждый пункт — 1-2 предложения максимум. Используй конкретные цифры из данных выше.

ГЛАВНАЯ ПРОБЛЕМА:
[Одна главная проблема которая больше всего вредит результату — с цифрами]

ЧТО УБИВАЕТ ДЕНЬГИ:
1. [Конкретный паттерн с цифрами]
2. [Конкретный паттерн с цифрами]
3. [Конкретный паттерн с цифрами]

ЧТО РАБОТАЕТ:
[Одна сильная сторона если есть, или "пока не видно сильных сторон"]

ТРИ ПРАВИЛА ПРЯМО СЕЙЧАС:
1. [Конкретное правило — не "улучши риск-менеджмент" а "не входи в BTC после 18:00"]
2. [Конкретное правило]
3. [Конкретное правило]

Пиши без markdown, без звёздочек, только чистый текст."""

        return prompt

    def _fallback_coaching(self, report: dict) -> str:
        """Правило-базированный коучинг если AI недоступен."""
        b  = report.get('basic', {})
        rr = report.get('rr', {})
        s  = report.get('streak', {})

        lines = ["Разбор на основе твоих данных:\n"]

        rr_ratio = rr.get('rr_ratio', 0)
        if rr_ratio < 1:
            lines.append(
                f"Главная проблема: R:R {rr_ratio} — ты зарабатываешь "
                f"${b.get('avg_win', 0):.2f} на победе и теряешь "
                f"${abs(b.get('avg_loss', 0)):.2f} на убытке. "
                f"Увеличь тейк-профит минимум в 2 раза."
            )

        if s.get('current_loss_streak', 0) >= 2:
            lines.append(
                f"Сейчас серия {s['current_loss_streak']} убытков подряд. "
                f"Сделай паузу минимум на час."
            )

        if b.get('winrate', 0) < 40:
            lines.append(
                f"Winrate {b.get('winrate')}% ниже нормы. "
                f"Ужесточи критерии входа — пропускай сомнительные сетапы."
            )

        if not lines[1:]:
            lines.append("Данных пока недостаточно для глубокого разбора. Продолжай торговать.")

        return "\n".join(lines)
