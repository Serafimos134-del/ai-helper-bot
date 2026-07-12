"""
tests/unit/test_exchange_adapters.py
Unit-тесты Exchange Adapter Layer (services/exchanges/, services/exchange_api.py).
"""

from unittest.mock import AsyncMock, patch

import pytest


def test_get_adapter_resolves_bingx():
    from services.exchanges.registry import get_adapter
    from services.exchanges.bingx import BingXAdapter
    assert isinstance(get_adapter('bingx'), BingXAdapter)
    assert isinstance(get_adapter(None), BingXAdapter)  # default fallback


@pytest.mark.parametrize("exchange", ["binance", "bybit", "okx", "mexc"])
def test_supported_but_unimplemented_exchanges_raise_clearly(exchange):
    from services.exchanges.registry import get_adapter, ExchangeNotImplementedError, SUPPORTED_EXCHANGES
    assert exchange in SUPPORTED_EXCHANGES
    with pytest.raises(ExchangeNotImplementedError, match="ещё не реализован"):
        get_adapter(exchange)


def test_unknown_exchange_raises():
    from services.exchanges.registry import get_adapter, ExchangeNotImplementedError
    with pytest.raises(ExchangeNotImplementedError):
        get_adapter('not_a_real_exchange')


@pytest.mark.asyncio
async def test_bingx_adapter_delegates_to_bingx_api():
    from services.exchanges.bingx import BingXAdapter
    adapter = BingXAdapter()
    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 42.0})):
        result = await adapter.get_balance()
    assert result['equity'] == 42.0


@pytest.mark.asyncio
async def test_facade_sequential_user_isolation_no_leak():
    """Тот же сценарий, что уже проверялся для BingX-специфичного
    contextvar-механизма в Этапе 1 миграции — теперь на уровне фасада."""
    from services.exchange_api import set_current_exchange, clear_current_exchange, get_balance
    from services.bingx_api import _get_credentials

    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 1.0})):
        clear_current_exchange()
        set_current_exchange('bingx', 'ALICE_KEY', 'ALICE_SECRET')
        await get_balance()
        assert _get_credentials() == ('ALICE_KEY', 'ALICE_SECRET')

        clear_current_exchange()
        set_current_exchange('bingx', 'BOB_KEY', 'BOB_SECRET')
        await get_balance()
        assert _get_credentials() == ('BOB_KEY', 'BOB_SECRET')

        # User without own keys must fall back to global .env, not inherit Bob's
        clear_current_exchange()
        set_current_exchange('bingx')
        assert _get_credentials() == ('GLOBAL_TEST_KEY', 'GLOBAL_TEST_SECRET')


@pytest.mark.asyncio
async def test_facade_defaults_to_bingx_when_never_set():
    from services.exchange_api import _adapter_var, get_balance
    _adapter_var.set(None)
    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 7.0})):
        result = await get_balance()
    assert result['equity'] == 7.0
