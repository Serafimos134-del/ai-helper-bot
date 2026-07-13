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


@pytest.mark.parametrize("exchange,adapter_cls_name", [
    ("bybit", "BybitAdapter"),
    ("binance", "BinanceAdapter"),
    ("mexc", "MEXCAdapter"),
])
def test_get_adapter_resolves_newly_implemented_exchanges(exchange, adapter_cls_name):
    """Задача от 13.07.2026 ("мультибиржевость обязательна") — Bybit/
    Binance/MEXC перестали быть заглушками ExchangeNotImplementedError."""
    from services.exchanges import registry
    adapter = registry.get_adapter(exchange)
    assert type(adapter).__name__ == adapter_cls_name


@pytest.mark.parametrize("exchange", ["okx"])
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
async def test_bybit_adapter_delegates_to_bybit_api():
    from services.exchanges.bybit import BybitAdapter
    adapter = BybitAdapter()
    with patch('services.bybit_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 11.0})):
        result = await adapter.get_balance()
    assert result['equity'] == 11.0


@pytest.mark.asyncio
async def test_binance_adapter_delegates_to_binance_api():
    from services.exchanges.binance import BinanceAdapter
    adapter = BinanceAdapter()
    with patch('services.binance_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 22.0})):
        result = await adapter.get_balance()
    assert result['equity'] == 22.0


@pytest.mark.asyncio
async def test_mexc_adapter_delegates_to_mexc_api():
    from services.exchanges.mexc import MEXCAdapter
    adapter = MEXCAdapter()
    with patch('services.mexc_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 33.0})):
        result = await adapter.get_balance()
    assert result['equity'] == 33.0


def test_bybit_symbol_normalization():
    from services.bybit_api import _to_bot_symbol, _to_exchange_symbol
    assert _to_bot_symbol('BTCUSDT') == 'BTC-USDT'
    assert _to_exchange_symbol('BTC-USDT') == 'BTCUSDT'


def test_binance_symbol_normalization():
    from services.binance_api import _to_bot_symbol, _to_exchange_symbol
    assert _to_bot_symbol('ETHUSDT') == 'ETH-USDT'
    assert _to_exchange_symbol('ETH-USDT') == 'ETHUSDT'


def test_mexc_symbol_normalization():
    from services.mexc_api import _to_bot_symbol, _to_exchange_symbol
    assert _to_bot_symbol('BTC_USDT') == 'BTC-USDT'
    assert _to_exchange_symbol('BTC-USDT') == 'BTC_USDT'


def test_binance_position_reconstruction_simple_round_trip():
    """Открытие + полное закрытие одним ордером каждое — простейший случай
    восстановления закрытой позиции из /userTrades (см. docstring
    services/binance_api.py — у Binance нет готового эндпоинта "закрытые
    позиции", в отличие от BingX/Bybit)."""
    from services.binance_api import _reconstruct_closed_positions
    trades = [
        {'side': 'BUY', 'qty': '1.0', 'price': '100.0', 'realizedPnl': '0', 'time': 1000},
        {'side': 'SELL', 'qty': '1.0', 'price': '110.0', 'realizedPnl': '10.0', 'time': 2000},
    ]
    positions = _reconstruct_closed_positions('BTC-USDT', trades, leverage=5)
    assert len(positions) == 1
    p = positions[0]
    assert p['side'] == 'LONG'
    assert p['entry_price'] == 100.0
    assert p['exit_price'] == 110.0
    assert p['realized_pnl'] == 10.0
    assert p['leverage'] == 5


@pytest.mark.asyncio
async def test_bybit_transport_error_message_not_swallowed():
    """Регрессия: _request() при транспортной ошибке (сеть/прокси/HTTP-статус
    без валидного тела) клал текст только в ключ 'error', а get_balance()
    читал 'retMsg' — реальная причина отказа терялась за общим "Неизвестная
    ошибка". Найдено на реальном тесте с ключами Bybit (13.07.2026)."""
    import services.bybit_api as bybit_api
    with patch.object(bybit_api, '_request_with_retry', new=AsyncMock(
        return_value=bybit_api._transport_error('403 Forbidden')
    )):
        result = await bybit_api.get_balance()
    assert result['success'] is False
    assert '403 Forbidden' in result['error']


@pytest.mark.asyncio
async def test_mexc_transport_error_message_not_swallowed():
    import services.mexc_api as mexc_api
    with patch.object(mexc_api, '_request_with_retry', new=AsyncMock(
        return_value=mexc_api._transport_error('Connection timed out')
    )):
        result = await mexc_api.get_balance()
    assert result['success'] is False
    assert 'Connection timed out' in result['error']


@pytest.mark.asyncio
async def test_bybit_non_ascii_key_gives_clear_message_not_generic_json_error():
    """Регрессия: не-ASCII символы в api_key/secret_key (случайно
    зацепившаяся кириллица при копировании) заставляли httpx падать с
    UnicodeEncodeError при кодировании HTTP-заголовка — подкласс ValueError,
    без отдельной ветки ловился бы как невнятное "Invalid JSON response:
    'ascii' codec can't encode...". Найдено на реальном тесте с ключами
    Bybit (13.07.2026). Основная защита теперь на входе (handlers/
    onboarding.py:_looks_like_key), это — второй рубеж на случай обхода."""
    import services.bybit_api as bybit_api

    class _RaisingClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **kw):
            raise UnicodeEncodeError('ascii', 'ключЯ', 5, 6, 'ordinal not in range(128)')

    bybit_api.set_bybit_credentials('KEYЯ', 'SECRET')
    with patch('httpx.AsyncClient', _RaisingClient):
        result = await bybit_api._request('/v5/account/wallet-balance', {'accountType': 'UNIFIED'})
    assert 'не ASCII' in result['retMsg'] or 'ASCII' in result['retMsg']
    bybit_api.clear_bybit_credentials()


def test_binance_position_reconstruction_dca_then_partial_closes():
    """Два входа (DCA) + два частичных выхода — проверяет средневзвешенные
    цены входа/выхода, а не только простой случай 1 сделка/1 сделка."""
    from services.binance_api import _reconstruct_closed_positions
    trades = [
        {'side': 'BUY', 'qty': '1.0', 'price': '100.0', 'realizedPnl': '0', 'time': 1000},
        {'side': 'BUY', 'qty': '1.0', 'price': '120.0', 'realizedPnl': '0', 'time': 1500},
        {'side': 'SELL', 'qty': '1.0', 'price': '130.0', 'realizedPnl': '15.0', 'time': 2000},
        {'side': 'SELL', 'qty': '1.0', 'price': '150.0', 'realizedPnl': '35.0', 'time': 2500},
    ]
    positions = _reconstruct_closed_positions('BTC-USDT', trades, leverage=3)
    assert len(positions) == 1
    p = positions[0]
    assert p['entry_price'] == 110.0  # (100+120)/2
    assert p['exit_price'] == 140.0   # (130+150)/2
    assert p['realized_pnl'] == 50.0
    assert p['quantity'] == 2.0


@pytest.mark.asyncio
async def test_facade_sequential_user_isolation_no_leak():
    """Тот же сценарий, что уже проверялся для BingX-специфичного
    contextvar-механизма в Этапе 1 миграции — теперь на уровне фасада.

    Регрессионный тест на реальный баг (найден на живом тесте с другого
    Telegram-аккаунта): подписчик без своих ключей должен получать ПУСТЫЕ
    credentials (запрос к бирже вернёт понятную ошибку авторизации), а НЕ
    неявный откат на глобальные .env-ключи — тот раньше применялся ко
    ВСЕМ без разбора и означал, что любой пользователь без своих ключей
    тихо видел РЕАЛЬНЫЙ баланс владельца. Явный .env-фолбэк остался
    только через set_owner_exchange() — см. tests/unit/test_access_control.py
    и core/user_context.py."""
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

        # User without own keys must NOT inherit Bob's, and must NOT fall
        # back to the owner's global .env keys either -- empty credentials,
        # any exchange call will cleanly fail with an auth error.
        clear_current_exchange()
        set_current_exchange('bingx')
        assert _get_credentials() == ('', '')


@pytest.mark.asyncio
async def test_facade_defaults_to_bingx_when_never_set():
    from services.exchange_api import _adapter_var, get_balance
    _adapter_var.set(None)
    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 7.0})):
        result = await get_balance()
    assert result['equity'] == 7.0


def test_set_owner_exchange_uses_global_env_keys():
    """set_owner_exchange() — единственный явный путь к глобальным
    .env-ключам, используется только owner-only кодом (core/user_context.py
    для самого владельца, core/scheduler.py для owner-only фоновых джоб)."""
    from services.exchange_api import set_owner_exchange, clear_current_exchange
    from services.bingx_api import _get_credentials
    clear_current_exchange()
    set_owner_exchange()
    assert _get_credentials() == ('GLOBAL_TEST_KEY', 'GLOBAL_TEST_SECRET')
    clear_current_exchange()
