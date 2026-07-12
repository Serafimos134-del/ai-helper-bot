"""
services/exchanges/registry.py
Реестр адаптеров бирж. get_adapter(exchange) отдаёт нужную реализацию
ExchangeAdapter — единственная точка, которую нужно расширить при
добавлении новой биржи (плюс сам класс адаптера), без изменения AI Core
или любого вызывающего кода.

SUPPORTED_EXCHANGES — полный список, заявленный в задаче от 12.07.2026:
"Поддерживаемые биржи: BingX, Binance, Bybit, OKX, MEXC". Реализован
пока только BingX (_ADAPTERS) — выбор недостающей биржи даёт понятную
ошибку (ExchangeNotImplementedError), а не тихий откат на BingX-код.
"""

from services.exchanges.base import ExchangeAdapter
from services.exchanges.bingx import BingXAdapter

DEFAULT_EXCHANGE = 'bingx'

SUPPORTED_EXCHANGES = ('bingx', 'binance', 'bybit', 'okx', 'mexc')

_ADAPTERS = {
    'bingx': BingXAdapter(),
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
