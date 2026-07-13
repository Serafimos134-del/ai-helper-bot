"""
services/exchanges/base.py
Exchange Adapter Layer — единый интерфейс для аккаунтных данных биржи
(баланс, позиции, ордера, история сделок), через который весь остальной
код (AI Core, хендлеры, фоновые джобы) обращается к бирже, не зная, какая
она у конкретного пользователя — BingX, Binance, Bybit, OKX или MEXC.

Задача от 12.07.2026: "Не создавать бизнес-логику, жёстко привязанную к
BingX. Сделать единый Exchange Adapter Layer." Реализованы BingX, Bybit,
Binance, MEXC (задача от 13.07.2026 — "мультибиржевость обязательна");
OKX сознательно не реализован (иная схема подписи запроса) — см.
services/exchanges/registry.py. BingX — единственная биржа, проверенная
на реальном торговом трафике; три остальные сверены с официальной
документацией, но ждут проверки на реальных ключах — см. docstring
соответствующих services/*_api.py.

Область интерфейса — намеренно только аккаунтные данные (явный список из
задачи: баланс/позиции/ордера/история сделок/PnL/риск-данные). Рыночные
данные для контекста AI (цена/тренд BTC/ETH, klines) остаются отдельным,
биржа-независимым по сути потоком через services/bingx_api.py напрямую
(ai/context_builder.py:_build_market_context) — это общерыночный
референс, не привязанный к тому, на какой бирже у пользователя аккаунт,
поэтому не входит в этот адаптер.

Формат возвращаемых данных — тот же, что уже отдавал services/bingx_api.py
(остальной код на него рассчитан) — при добавлении новой биржи адаптер
должен сам привести её ответ к этому же формату, не меняя вызывающий код.
"""

from abc import ABC, abstractmethod


class ExchangeAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def set_credentials(self, api_key: str, secret_key: str) -> None:
        """Устанавливает ключи текущего пользователя для этого asyncio-таска
        (contextvar-механизм — см. конкретную реализацию)."""

    @abstractmethod
    def clear_credentials(self) -> None:
        """Сбрасывает ключи — обязателен вызов первым делом в middleware на
        каждый апдевт (core/user_context.py), иначе возможна утечка ключей
        между последовательно обрабатываемыми апдейтами разных
        пользователей (см. docstring resolve_user_context)."""

    @abstractmethod
    async def validate_keys(self, api_key: str, secret_key: str) -> dict:
        """Проверяет ключи реальным запросом к бирже, не трогая текущие
        credentials контекста. Возвращает как минимум {'success': bool}."""

    @abstractmethod
    async def get_balance(self) -> dict:
        """{'success': bool, 'equity': float, 'available': float,
        'used_margin': float, 'unrealized_pnl': float, 'currency': str}"""

    @abstractmethod
    async def get_open_positions(self) -> dict:
        """{'success': bool, 'trades': [{'symbol','side','entry_price'
        (или 'entryPrice'),'unrealized_pnl','leverage','size'/'quantity',
        'stop_loss','take_profit', ...}]}"""

    @abstractmethod
    async def get_closed_orders(self, symbol: str = '', limit: int = 20) -> dict:
        """Исполнения ордеров (не позиций) — {'success': bool, 'trades': [...]}"""

    @abstractmethod
    async def get_recent_closed_positions(self, limit: int = 20) -> dict:
        """Закрытые ПОЗИЦИИ (цена входа/выхода, реализованный PnL, плечо) —
        {'success': bool, 'positions': [{'positionId','symbol','side',
        'entry_price','exit_price','quantity','realized_pnl','leverage',
        'open_time','close_time'}]}"""
