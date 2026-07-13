"""
tests/integration/test_account_data.py
Интеграционный тест: получение данных аккаунта (баланс/позиции/история)
через Exchange Adapter Layer с реально резолвленным пользователем.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_get_balance_uses_resolved_user_credentials(db, make_user):
    from services.exchange_api import set_current_exchange, clear_current_exchange, get_balance
    user = make_user('901')
    db.set_bingx_keys(user['user_id'], 'U901_KEY', 'U901_SECRET')
    api_key, secret_key = db.get_bingx_keys(user['user_id'])

    clear_current_exchange()
    set_current_exchange('bingx', api_key, secret_key)
    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 555.0})):
        result = await get_balance()
    assert result['equity'] == 555.0
    from services.bingx_api import _get_credentials
    assert _get_credentials() == ('U901_KEY', 'U901_SECRET')


@pytest.mark.asyncio
async def test_history_import_persists_positions_scoped_to_user(db, make_user):
    from services.history_import import import_trade_history
    user = make_user('902')

    fake_positions = {
        'success': True,
        'positions': [{
            'positionId': 'POS_902', 'symbol': 'BTC-USDT', 'side': 'LONG',
            'entry_price': 50000, 'exit_price': 51000, 'quantity': 0.01,
            'realized_pnl': 10, 'leverage': 5, 'open_time': 1000, 'close_time': 2000,
        }]
    }
    with patch('services.history_import.get_recent_closed_positions', new=AsyncMock(return_value=fake_positions)):
        result = await import_trade_history(db, user['user_id'])

    assert result['imported'] == 1
    trades = db.get_closed_trades(user_id=user['user_id'])
    assert len(trades) == 1
    assert trades[0]['orderId'] == 'hist_POS_902'


@pytest.mark.asyncio
async def test_history_import_deduplicates_on_rerun(db, make_user):
    from services.history_import import import_trade_history
    user = make_user('903')
    fake = {'success': True, 'positions': [{
        'positionId': 'POS_903', 'symbol': 'ETH-USDT', 'side': 'SHORT',
        'entry_price': 3000, 'exit_price': 2900, 'quantity': 1,
        'realized_pnl': 100, 'leverage': 5, 'open_time': 1000, 'close_time': 2000,
    }]}
    with patch('services.history_import.get_recent_closed_positions', new=AsyncMock(return_value=fake)):
        first = await import_trade_history(db, user['user_id'])
        second = await import_trade_history(db, user['user_id'])
    assert first['imported'] == 1
    assert second['imported'] == 0
    assert second['skipped'] == 1


@pytest.mark.asyncio
async def test_open_positions_snapshot_used_by_status(db, make_user):
    """Проверяет, что status_command берёт открытые позиции строго своего
    пользователя (см. handlers/system.py:status_command)."""
    import handlers.system as sysmod
    from unittest.mock import MagicMock
    user = make_user('904')
    db.set_bingx_keys(user['user_id'], 'U904_KEY', 'U904_SECRET')
    db.add_open_trade({
        'orderId': 'OPEN904', 'symbol': 'SOL-USDT', 'side': 'LONG',
        'entry_price': 150, 'quantity': 1, 'leverage': 5, 'user_id': user['user_id'],
    })

    ctx = MagicMock()
    ctx.user_data = {'user': user, 'is_owner': False, 'is_authorized': True}
    upd = MagicMock()
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()

    orig_get_db = sysmod.get_db
    sysmod.get_db = lambda: db
    try:
        with patch('handlers.system.get_balance', new=AsyncMock(return_value={
            'success': True, 'equity': 500.0, 'available': 400.0, 'used_margin': 100.0, 'unrealized_pnl': 0.0
        })):
            await sysmod.status_command(upd, ctx)
    finally:
        sysmod.get_db = orig_get_db

    text = upd.message.reply_text.call_args.args[0]
    assert 'SOL-USDT' in text


@pytest.mark.asyncio
async def test_show_balance_without_keys_prompts_setkeys_not_exchange_call(db, make_user):
    """Регрессия на реальный баг: подписчик без ключей, нажав «Баланс»,
    раньше тихо получал баланс владельца через .env-фолбэк. Теперь должен
    получить понятную подсказку, а запрос к бирже вообще не должен уйти."""
    from unittest.mock import MagicMock
    import handlers.trading as trading
    user = make_user('905')  # no BingX keys linked

    ctx = MagicMock()
    ctx.user_data = {'user': user, 'is_owner': False}
    upd = MagicMock()
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()

    orig_get_db = trading.get_db
    trading.get_db = lambda: db
    try:
        with patch('handlers.trading.get_balance', new=AsyncMock()) as balance_mock:
            await trading.show_balance(upd, ctx)
    finally:
        trading.get_db = orig_get_db

    balance_mock.assert_not_called()
    text = upd.message.reply_text.call_args.args[0]
    assert '/setkeys' in text


@pytest.mark.asyncio
async def test_show_balance_owner_without_keys_still_works(db, owner_user):
    """Владелец без привязанных своих ключей — переходный период — всё
    ещё должен видеть баланс (через явный set_owner_exchange, не общий
    .env-фолбэк, который раньше применялся ко всем)."""
    from unittest.mock import MagicMock
    import handlers.trading as trading
    ctx = MagicMock()
    ctx.user_data = {'user': owner_user, 'is_owner': True}
    upd = MagicMock()
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))

    orig_get_db = trading.get_db
    trading.get_db = lambda: db
    try:
        with patch('handlers.trading.get_balance', new=AsyncMock(return_value={
            'success': True, 'equity': 1000.0, 'available': 900.0, 'used_margin': 100.0, 'unrealized_pnl': 0.0
        })) as balance_mock:
            await trading.show_balance(upd, ctx)
    finally:
        trading.get_db = orig_get_db

    balance_mock.assert_called_once()
