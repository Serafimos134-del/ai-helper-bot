"""
services/exchanges/registry.py
Реестр адаптеров бирж. get_adapter(exchange) отдаёт нужную реализацию
ExchangeAdapter — единственная точка, которую нужно расширить при
добавлении новой биржи (плюс сам класс адаптера), без изменения AI Core
или любого вызывающего кода.

SUPPORTED_EXCHANGES — полный список, заявленный в задаче от 12.07.2026:
"Поддерживаемые биржи: BingX, Binance, Bybit, OKX, MEXC". Реализованы
BingX, Bybit, Binance, MEXC (задача от 13.07.2026 — "мультибиржевость
обязательна") — BingX единственная, проверенная на реальном трафике,
остальные три сверены с официальной документацией, но ждут проверки на
реальных ключах (см. docstring соответствующих services/*_api.py). OKX
сознательно не реализован — иная схема подписи запроса (timestamp +
passphrase, не только HMAC ключ/секрет), выбор OKX даёт понятную ошибку,
а не тихий откат на чужой код.
"""

from services.exchanges.base import ExchangeAdapter
from services.exchanges.bingx import BingXAdapter
from services.exchanges.bybit import BybitAdapter
from services.exchanges.binance import BinanceAdapter
from services.exchanges.mexc import MEXCAdapter

DEFAULT_EXCHANGE = 'bingx'

SUPPORTED_EXCHANGES = ('bingx', 'binance', 'bybit', 'okx', 'mexc')

_ADAPTERS = {
    'bingx': BingXAdapter(),
    'bybit': BybitAdapter(),
    'binance': BinanceAdapter(),
    'mexc': MEXCAdapter(),
}


class ExchangeNotImplementedError(Exception):
    pass


def get_adapter(exchange: str) -> ExchangeAdapter:
    exchange = (exchange or DEFAULT_EXCHANGE).lower()
    adapter = _ADAPTERS.get(exchange)
    if adapter is not None:
        return adapter
    if exchange in SUPPORTED_EXCHANGES:
        raise ExchangeNotImplementedError(
            f"Биржа «{exchange}» в списке поддерживаемых, но адаптер для неё ещё не реализован."
        )
    raise ExchangeNotImplementedError(f"Неизвестная биржа «{exchange}».")
