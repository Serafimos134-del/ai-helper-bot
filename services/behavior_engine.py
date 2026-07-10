"""
services/behavior_engine.py
Behavior Alerts Engine — детектирует деструктивные паттерны поведения трейдера.
Чистая дата-аналитика, без AI (детерминированные правила).
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Пороги (тюнятся под реальных пользователей) ───
REVENGE_LOSS_STREAK = 2
REVENGE_SIZE_MULTIPLIER = 1.5

OVERTRADING_WINDOW_HOURS = 2
OVERTRADING_MAX_TRADES = 3

PANIC_CLOSE_MAX_MINUTES = 5
PANIC_CLOSE_SL_TOLERANCE = 0.003

FOMO_CHANGE_THRESHOLD = 3.0


class BehaviorEngine:
    """Детектирует деструктивные паттерны поведения трейдера."""

    def __init__(self, db):
        self.db = db

    def detect_revenge_trading(self, user_id: str, new_trade: dict) -> Optional[dict]:
        """Крупная позиция сразу после серии убытков = попытка отыграться."""
        recent = self.db.get_closed_trades(limit=REVENGE_LOSS_STREAK, user_id=user_id)
        if len(recent) < REVENGE_LOSS_STREAK:
            return None
        if not all(float(t['realized_pnl']) < 0 for t in recent):
            return None

        history = self.db.get_closed_trades(limit=20, user_id=user_id)
        if len(history) < 3:
            return None

        avg_value = sum(float(t['entry_price']) * float(t['quantity']) for t in history) / len(history)
        if avg_value == 0:
            return None

        new_value = float(new_trade.get('entryPrice', 0)) * float(
            new_trade.get('positionAmt', new_trade.get('size', 0))
        )
        ratio = new_value / avg_value

        if ratio >= REVENGE_SIZE_MULTIPLIER:
            return {
                'event_type': 'revenge_trading',
                'severity': 'high' if ratio >= 2.5 else 'medium',
                'metadata': {
                    'loss_streak': REVENGE_LOSS_STREAK,
                    'size_ratio': round(ratio, 2),
                    'symbol': new_trade.get('symbol')
                }
            }
        return None

    def detect_overtrading(self, user_id: str) -> Optional[dict]:
        """Слишком много входов за короткое окно времени."""
        since = datetime.now(timezone.utc) - timedelta(hours=OVERTRADING_WINDOW_HOURS)
        count = 0

        for t in self.db.get_closed_trades(limit=50, user_id=user_id):
            open_time = t.get('open_time')
            if not open_time:
                continue
            try:
                ot = datetime.fromisoformat(str(open_time))
                if ot.tzinfo is None:
                    ot = ot.replace(tzinfo=timezone.utc)
                if ot >= since:
                    count += 1
            except Exception:
                continue

        for t in self.db.get_open_trades(user_id=user_id):
            created = t.get('created_at')
            if not created:
                continue
            try:
                ot = datetime.fromisoformat(str(created))
                if ot.tzinfo is None:
                    ot = ot.replace(tzinfo=timezone.utc)
                if ot >= since:
                    count += 1
            except Exception:
                continue

        if count > OVERTRADING_MAX_TRADES:
            return {
                'event_type': 'overtrading',
                'severity': 'high' if count > OVERTRADING_MAX_TRADES * 2 else 'medium',
                'metadata': {'trades_count': count, 'window_hours': OVERTRADING_WINDOW_HOURS}
            }
        return None

    def detect_panic_close(self, closed_trade: dict) -> Optional[dict]:
        """Быстрое закрытие в убыток без срабатывания SL = эмоциональное решение."""
        holding = closed_trade.get('holding_minutes')
        pnl = float(closed_trade.get('realized_pnl', 0))

        if holding is None or holding > PANIC_CLOSE_MAX_MINUTES:
            return None
        if pnl >= 0:
            return None

        stop_loss = closed_trade.get('stop_loss')
        exit_price = float(closed_trade.get('exit_price', 0))
        if stop_loss:
            try:
                sl = float(stop_loss)
                if sl > 0 and abs(exit_price - sl) / sl <= PANIC_CLOSE_SL_TOLERANCE:
                    return None
            except (ValueError, ZeroDivisionError):
                pass

        return {
            'event_type': 'panic_close',
            'severity': 'medium',
            'metadata': {
                'symbol': closed_trade.get('symbol'),
                'holding_minutes': holding,
                'pnl': pnl
            }
        }

    def detect_fomo(self, new_trade: dict, kline_data: list) -> Optional[dict]:
        """Вход после резкого движения цены в направлении сделки = погоня за рынком."""
        if not kline_data or len(kline_data) < 2:
            return None
        try:
            price_1h_ago = float(kline_data[-2].get('close', kline_data[-2].get('c', 0)))
            price_now = float(kline_data[-1].get('close', kline_data[-1].get('c', 0)))
        except (ValueError, IndexError, TypeError):
            return None
        if price_1h_ago == 0:
            return None

        change_pct = (price_now - price_1h_ago) / price_1h_ago * 100
        side = new_trade.get('side', '')
        chasing = (side == 'LONG' and change_pct >= FOMO_CHANGE_THRESHOLD) or \
                  (side == 'SHORT' and change_pct <= -FOMO_CHANGE_THRESHOLD)

        if chasing:
            return {
                'event_type': 'fomo',
                'severity': 'high' if abs(change_pct) >= 5 else 'medium',
                'metadata': {
                    'symbol': new_trade.get('symbol'),
                    'side': side,
                    'price_change_1h': round(change_pct, 2)
                }
            }
        return None

    def save_event(self, user_id: str, event: dict, order_id: str = None):
        try:
            self.db.add_behavior_event(
                user_id,
                event['event_type'],
                event['severity'],
                json.dumps(event['metadata'], ensure_ascii=False),
                order_id=str(order_id) if order_id else None,
            )
        except Exception as e:
            logger.error(f"Не удалось сохранить behavior_event: {e}")


def format_alert(event: dict) -> str:
    """Форматирует событие в читаемое сообщение для Telegram."""
    event_type = event['event_type']
    meta = event['metadata']
    emoji = '🔴' if event['severity'] == 'high' else '🟡'

    if event_type == 'revenge_trading':
        return (
            f"{emoji} Revenge Trading\n\n"
            f"После {meta['loss_streak']} убытков подряд новая позиция по {meta['symbol']} "
            f"в {meta['size_ratio']}x больше обычного размера.\n\n"
            f"Классический признак попытки отыграться. Уменьши размер позиции до обычного уровня "
            f"или сделай паузу."
        )
    if event_type == 'overtrading':
        return (
            f"{emoji} Overtrading\n\n"
            f"{meta['trades_count']} сделок за последние {meta['window_hours']} часа — выше нормы.\n\n"
            f"Частые входы обычно означают потерю дисциплины, а не появление реальных возможностей. "
            f"Сделай перерыв минимум на час."
        )
    if event_type == 'panic_close':
        return (
            f"{emoji} Panic Close\n\n"
            f"Позиция по {meta['symbol']} закрыта через {meta['holding_minutes']} мин в убыток "
            f"${meta['pnl']:.2f}, без срабатывания стоп-лосса.\n\n"
            f"Похоже на эмоциональное закрытие. Ставь стоп-лосс заранее и следуй ему."
        )
    if event_type == 'fomo':
        return (
            f"{emoji} FOMO\n\n"
            f"Вход в {meta['side']} по {meta['symbol']} после движения цены "
            f"{meta['price_change_1h']:+.2f}% за последний час.\n\n"
            f"Похоже на погоню за движением. Жди коррекции или подтверждения."
        )
    return f"{emoji} {event_type}"
