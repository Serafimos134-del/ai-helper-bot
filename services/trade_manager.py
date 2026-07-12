"""
services/trade_manager.py
Управление жизненным циклом сделки: идея, инвалидация, DCA, TP-зоны.
"""

import json
from services.database import Database

class TradeManager:
    def __init__(self, db: Database = None):
        self.db = db or Database()

    def set_idea(self, order_id: str, idea: str, invalidation_sl: float = None,
                 tp_zones: list = None):
        """Установить торговую идею и опционально уровень инвалидации и TP-зоны."""
        updates = {'idea': idea}
        if invalidation_sl is not None:
            updates['invalidation_sl'] = invalidation_sl
        if tp_zones:
            updates['tp_zones'] = json.dumps(tp_zones)
        self.db.update_open_trade_by_order_id(order_id, **updates)

    def is_invalidated(self, order_id: str, current_price: float, user_id: str = 'default') -> bool:
        """Проверить, сломана ли идея по цене."""
        trade = self._get_open_by_order(order_id, user_id)
        if not trade or not trade.get('invalidation_sl'):
            return False
        sl = float(trade['invalidation_sl'])
        side = trade.get('side', 'LONG')
        if side == 'LONG':
            return current_price <= sl
        else:
            return current_price >= sl

    def get_tp_zones(self, order_id: str, user_id: str = 'default') -> list:
        """Вернуть список TP-зон."""
        trade = self._get_open_by_order(order_id, user_id)
        if not trade or not trade.get('tp_zones'):
            return []
        try:
            return json.loads(trade['tp_zones'])
        except json.JSONDecodeError:
            return []

    def can_dca(self, order_id: str, max_dca: int = 2, user_id: str = 'default') -> bool:
        """Проверить, можно ли сделать добор."""
        trade = self._get_open_by_order(order_id, user_id)
        if not trade:
            return False
        return int(trade.get('dca_count', 0)) < max_dca

    def add_dca(self, order_id: str, user_id: str = 'default'):
        """Зафиксировать добор."""
        trade = self._get_open_by_order(order_id, user_id)
        if trade:
            new_count = int(trade.get('dca_count', 0)) + 1
            self.db.update_open_trade_by_order_id(order_id, dca_count=new_count)

    def _get_open_by_order(self, order_id: str, user_id: str = 'default') -> dict:
        """Внутренний метод для получения открытой сделки по orderId. user_id
        обязателен для не-владельца — иначе поиск шёл только по 'default'
        (сделкам владельца) и молча не находил сделки остальных
        пользователей: TP-зоны/DCA-лимит просто не работали для всех, кроме
        владельца (найдено при аудите /status, см. handlers/system.py)."""
        trades = self.db.get_open_trades(user_id=user_id)
        for t in trades:
            if t.get('orderId') == order_id:
                return t
        return None