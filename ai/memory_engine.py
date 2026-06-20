"""
Memory Engine — долгосрочная память трейдера.
Отслеживает win rate по тикерам, направлениям, времени удержания.
"""
import logging
from services.database import Database

logger = logging.getLogger(__name__)


class MemoryEngine:
    """Обёртка над Database для агрегированной торговой статистики."""

    def __init__(self):
        self.db = Database()

    async def update(self, closed_trade: dict) -> None:
        """Обновляет статистику после закрытия сделки."""
        try:
            symbol = closed_trade.get('symbol', '')
            side = closed_trade.get('side', '')
            pnl = float(closed_trade.get('realized_pnl', 0))
            holding_minutes = closed_trade.get('holding_minutes')

            self._increment('global', 'total_trades')

            if pnl > 0:
                self._increment('global', 'winning_trades')
            else:
                self._increment('global', 'losing_trades')

            if symbol:
                self._increment('ticker', f'{symbol}_total')
                if pnl > 0:
                    self._increment('ticker', f'{symbol}_wins')

            if side:
                self._increment('direction', f'{side}_total')
                if pnl > 0:
                    self._increment('direction', f'{side}_wins')

            if holding_minutes is not None:
                current_avg = float(self.db.memory_get('holding', 'avg_minutes') or 0)
                total = int(self.db.memory_get('global', 'total_trades') or 1)
                new_avg = (current_avg * (total - 1) + holding_minutes) / total
                self.db.memory_set('holding', 'avg_minutes', new_avg)

        except Exception as e:
            logger.error(f"MemoryEngine update error: {e}")

    def _increment(self, category: str, key: str, value: float = 1.0) -> None:
        current = float(self.db.memory_get(category, key) or 0)
        self.db.memory_set(category, key, current + value)