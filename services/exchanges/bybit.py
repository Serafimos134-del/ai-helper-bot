"""
services/exchanges/bybit.py
Тонкая обёртка над services/bybit_api.py — тот же паттерн, что и
services/exchanges/bingx.py.
"""

from services.exchanges.base import ExchangeAdapter
from services import bybit_api


class BybitAdapter(ExchangeAdapter):
    name = "bybit"

    def set_credentials(self, api_key: str, secret_key: str) -> None:
        bybit_api.set_bybit_credentials(api_key, secret_key)

    def clear_credentials(self) -> None:
        bybit_api.clear_bybit_credentials()

    async def validate_keys(self, api_key: str, secret_key: str) -> dict:
        return await bybit_api.validate_keys(api_key, secret_key)

    async def get_balance(self) -> dict:
        return await bybit_api.get_balance()

    async def get_open_positions(self) -> dict:
        return await bybit_api.get_open_positions()

    async def get_closed_orders(self, symbol: str = '', limit: int = 20) -> dict:
        return await bybit_api.get_closed_orders(symbol, limit)

    async def get_recent_closed_positions(self, limit: int = 20) -> dict:
        return await bybit_api.get_recent_closed_positions(limit)
