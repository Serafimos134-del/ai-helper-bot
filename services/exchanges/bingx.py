"""
services/exchanges/bingx.py
BingX — единственная полностью рабочая реализация ExchangeAdapter на
момент MVP. Тонкая обёртка над services/bingx_api.py: вся реальная логика
(подпись запросов, разбор ответов, contextvars-изоляция ключей) остаётся
там же, не дублируется и не переписывается — задача явно требовала
единый Adapter Layer поверх существующего кода, не рефакторинг BingX-слоя.
"""

from services.exchanges.base import ExchangeAdapter
from services import bingx_api


class BingXAdapter(ExchangeAdapter):
    name = "bingx"

    def set_credentials(self, api_key: str, secret_key: str) -> None:
        bingx_api.set_bingx_credentials(api_key, secret_key)

    def clear_credentials(self) -> None:
        bingx_api.clear_bingx_credentials()

    async def validate_keys(self, api_key: str, secret_key: str) -> dict:
        return await bingx_api.validate_keys(api_key, secret_key)

    async def get_balance(self) -> dict:
        return await bingx_api.get_balance()

    async def get_open_positions(self) -> dict:
        return await bingx_api.get_open_positions()

    async def get_closed_orders(self, symbol: str = '', limit: int = 20) -> dict:
        return await bingx_api.get_closed_orders(symbol, limit)

    async def get_recent_closed_positions(self, limit: int = 20) -> dict:
        return await bingx_api.get_recent_closed_positions(limit)
