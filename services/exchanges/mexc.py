"""
services/exchanges/mexc.py
Тонкая обёртка над services/mexc_api.py — тот же паттерн, что и
services/exchanges/bingx.py.
"""

from services.exchanges.base import ExchangeAdapter
from services import mexc_api


class MEXCAdapter(ExchangeAdapter):
    name = "mexc"

    def set_credentials(self, api_key: str, secret_key: str) -> None:
        mexc_api.set_mexc_credentials(api_key, secret_key)

    def clear_credentials(self) -> None:
        mexc_api.clear_mexc_credentials()

    async def validate_keys(self, api_key: str, secret_key: str) -> dict:
        return await mexc_api.validate_keys(api_key, secret_key)

    async def get_balance(self) -> dict:
        return await mexc_api.get_balance()

    async def get_open_positions(self) -> dict:
        return await mexc_api.get_open_positions()

    async def get_closed_orders(self, symbol: str = '', limit: int = 20) -> dict:
        return await mexc_api.get_closed_orders(symbol, limit)

    async def get_recent_closed_positions(self, limit: int = 20) -> dict:
        return await mexc_api.get_recent_closed_positions(limit)
