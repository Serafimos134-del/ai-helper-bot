"""
services/performance_engine.py
Performance Engine — глубокая аналитика торговли без AI.
Считает реальные метрики: winrate, RR, loss streak, session performance, setup performance.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class PerformanceEngine:
    """Вычисляет продвинутые метрики производительности трейдера."""

    def __init__(self, db):
        self.db = db

    def get_full_report(self, user_id: str = 'default') -> dict:
        """Полный отчёт по всем метрикам."""
        trades = self.db.get_closed_trades(limit=200, user_id=user_id)
        if not trades:
            return {'empty': True}

        return {
            'basic':    self._basic_metrics(trades),
            'streak':   self._streak_analysis(trades),
            'session':  self._session_performance(trades),
            'setup':    self._setup_performance(trades),
            'rr':       self._risk_reward_analysis(trades),
            'leverage': self._leverage_analysis(trades),
        }

    def _basic_metrics(self, trades: list) -> dict:
        total  = len(trades)
        wins   = [t for t in trades if float(t['realized_pnl']) > 0]
        losses = [t for t in trades if float(t['realized_pnl']) < 0]

        avg_win  = sum(float(t['realized_pnl']) for t in wins)  / len(wins)  if wins  else 0
        avg_loss = sum(float(t['realized_pnl']) for t in losses) / len(losses) if losses else 0
        total_pnl = sum(float(t['realized_pnl']) for t in trades)

        profit_factor = (
            abs(sum(float(t['realized_pnl']) for t in wins) /
                sum(float(t['realized_pnl']) for t in losses))
            if losses and sum(float(t['realized_pnl']) for t in losses) != 0 else 0
        )

        return {
            'total':         total,
            'wins':          len(wins),
            'losses':        len(losses),
            'winrate':       round(len(wins) / total * 100, 1) if total else 0,
            'avg_win':       round(avg_win, 2),
            'avg_loss':      round(avg_loss, 2),
            'total_pnl':     round(total_pnl, 2),
            'profit_factor': round(profit_factor, 2),
        }

    def _streak_analysis(self, trades: list) -> dict:
        """Анализ серий выигрышей и проигрышей."""
        sorted_trades = sorted(trades, key=lambda t: t.get('close_time') or '', reverse=False)

        current_win_streak  = 0
        current_loss_streak = 0
        max_win_streak      = 0
        max_loss_streak     = 0
        current_streak_type = None

        for t in sorted_trades:
            pnl = float(t['realized_pnl'])
            if pnl > 0:
                if current_streak_type == 'win':
                    current_win_streak += 1
                else:
                    current_win_streak  = 1
                    current_loss_streak = 0
                current_streak_type = 'win'
                max_win_streak = max(max_win_streak, current_win_streak)
            elif pnl < 0:
                if current_streak_type == 'loss':
                    current_loss_streak += 1
                else:
                    current_loss_streak = 1
                    current_win_streak  = 0
                current_streak_type = 'loss'
                max_loss_streak = max(max_loss_streak, current_loss_streak)

        return {
            'current_win_streak':  current_win_streak  if current_streak_type == 'win'  else 0,
            'current_loss_streak': current_loss_streak if current_streak_type == 'loss' else 0,
            'max_win_streak':      max_win_streak,
            'max_loss_streak':     max_loss_streak,
        }

    def _session_performance(self, trades: list) -> dict:
        """Производительность по времени суток (UTC)."""
        sessions = {
            'morning':   {'label': 'Утро (6-12)',    'wins': 0, 'total': 0, 'pnl': 0},
            'afternoon': {'label': 'День (12-18)',   'wins': 0, 'total': 0, 'pnl': 0},
            'evening':   {'label': 'Вечер (18-24)',  'wins': 0, 'total': 0, 'pnl': 0},
            'night':     {'label': 'Ночь (0-6)',     'wins': 0, 'total': 0, 'pnl': 0},
        }

        for t in trades:
            close_time = t.get('close_time')
            if not close_time:
                continue
            try:
                dt = datetime.fromisoformat(str(close_time))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                hour = dt.hour
                if   6  <= hour < 12: s = 'morning'
                elif 12 <= hour < 18: s = 'afternoon'
                elif 18 <= hour < 24: s = 'evening'
                else:                 s = 'night'

                pnl = float(t['realized_pnl'])
                sessions[s]['total'] += 1
                sessions[s]['pnl']   += pnl
                if pnl > 0:
                    sessions[s]['wins'] += 1
            except Exception:
                continue

        result = {}
        for key, s in sessions.items():
            if s['total'] > 0:
                result[key] = {
                    'label':   s['label'],
                    'total':   s['total'],
                    'winrate': round(s['wins'] / s['total'] * 100, 1),
                    'pnl':     round(s['pnl'], 2),
                }
        return result

    def _setup_performance(self, trades: list) -> dict:
        """Производительность по символам."""
        symbols = {}
        for t in trades:
            sym = t.get('symbol', 'Unknown')
            pnl = float(t['realized_pnl'])
            if sym not in symbols:
                symbols[sym] = {'wins': 0, 'total': 0, 'pnl': 0}
            symbols[sym]['total'] += 1
            symbols[sym]['pnl']   += pnl
            if pnl > 0:
                symbols[sym]['wins'] += 1

        result = {}
        for sym, data in symbols.items():
            if data['total'] >= 2:  # показываем только если есть минимум 2 сделки
                result[sym] = {
                    'total':   data['total'],
                    'winrate': round(data['wins'] / data['total'] * 100, 1),
                    'pnl':     round(data['pnl'], 2),
                }
        return dict(sorted(result.items(), key=lambda x: x[1]['pnl'], reverse=True))

    def _risk_reward_analysis(self, trades: list) -> dict:
        """Анализ Risk/Reward."""
        wins   = [float(t['realized_pnl']) for t in trades if float(t['realized_pnl']) > 0]
        losses = [abs(float(t['realized_pnl'])) for t in trades if float(t['realized_pnl']) < 0]

        avg_win  = sum(wins)   / len(wins)   if wins   else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        rr_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

        return {
            'avg_win':  round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'rr_ratio': rr_ratio,
            'rr_status': (
                'хороший ✅' if rr_ratio >= 1.5 else
                'приемлемый 🟡' if rr_ratio >= 1.0 else
                'низкий ❌'
            )
        }

    def _leverage_analysis(self, trades: list) -> dict:
        """Анализ использования плеча."""
        leverages = [float(t.get('leverage', 1)) for t in trades if t.get('leverage')]
        if not leverages:
            return {}

        wins_lev  = [float(t.get('leverage', 1)) for t in trades if float(t['realized_pnl']) > 0]
        loss_lev  = [float(t.get('leverage', 1)) for t in trades if float(t['realized_pnl']) < 0]

        return {
            'avg_leverage':      round(sum(leverages) / len(leverages), 1),
            'avg_win_leverage':  round(sum(wins_lev)  / len(wins_lev),  1) if wins_lev  else 0,
            'avg_loss_leverage': round(sum(loss_lev)  / len(loss_lev),  1) if loss_lev  else 0,
        }


def format_performance_report(report: dict) -> str:
    """Форматирует полный отчёт в читаемый текст для Telegram."""
    if report.get('empty'):
        return "📊 Нет данных для анализа. Закрой хотя бы несколько сделок."

    lines = ["📊 Performance Report\n"]

    # Basic
    b = report.get('basic', {})
    lines.append(
        f"Сделок: {b.get('total', 0)} | "
        f"Winrate: {b.get('winrate', 0)}% | "
        f"PNL: ${b.get('total_pnl', 0):+.2f}"
    )
    lines.append(
        f"Avg win: ${b.get('avg_win', 0):.2f} | "
        f"Avg loss: ${b.get('avg_loss', 0):.2f} | "
        f"Profit factor: {b.get('profit_factor', 0):.2f}"
    )

    # RR
    rr = report.get('rr', {})
    if rr:
        lines.append(f"R:R — {rr.get('rr_ratio', 0)} ({rr.get('rr_status', '')})")

    # Streak
    s = report.get('streak', {})
    if s:
        lines.append("")
        lines.append("🔁 Серии:")
        if s.get('current_loss_streak', 0) > 0:
            lines.append(f"  Текущая серия убытков: {s['current_loss_streak']}")
        if s.get('current_win_streak', 0) > 0:
            lines.append(f"  Текущая серия побед: {s['current_win_streak']}")
        lines.append(f"  Макс серия побед: {s.get('max_win_streak', 0)}")
        lines.append(f"  Макс серия убытков: {s.get('max_loss_streak', 0)}")

    # Session
    sess = report.get('session', {})
    if sess:
        lines.append("")
        lines.append("⏰ По времени суток:")
        for key in ['morning', 'afternoon', 'evening', 'night']:
            if key in sess:
                d = sess[key]
                pnl_str = f"${d['pnl']:+.2f}"
                lines.append(
                    f"  {d['label']}: {d['winrate']}% winrate | "
                    f"PNL {pnl_str} | {d['total']} сделок"
                )

    # Leverage
    lev = report.get('leverage', {})
    if lev:
        lines.append("")
        lines.append(
            f"⚡️ Плечо: avg {lev.get('avg_leverage', 0)}x | "
            f"в победах {lev.get('avg_win_leverage', 0)}x | "
            f"в убытках {lev.get('avg_loss_leverage', 0)}x"
        )

    # Setup (top 5)
    setup = report.get('setup', {})
    if setup:
        lines.append("")
        lines.append("🎯 По монетам (топ 5):")
        for sym, d in list(setup.items())[:5]:
            pnl_str = f"${d['pnl']:+.2f}"
            lines.append(
                f"  {sym}: {d['winrate']}% winrate | "
                f"PNL {pnl_str} | {d['total']} сделок"
            )

    return "\n".join(lines)
