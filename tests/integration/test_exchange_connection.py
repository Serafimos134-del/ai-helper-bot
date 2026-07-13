"""
tests/integration/test_exchange_connection.py
Интеграционный тест: подключение биржи через /setkeys (валидация ключей,
сохранение, авто-запуск фонового импорта истории).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(text=None):
    u = MagicMock()
    u.effective_chat.send_message = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    u.message = MagicMock()
    u.message.text = text
    u.message.reply_text = AsyncMock()
    u.message.delete = AsyncMock()
    return u


def _make_ctx(user):
    c = MagicMock()
    c.user_data = {'user': user}
    return c


@pytest.mark.asyncio
async def test_setkeys_success_flow_stores_encrypted_keys(db, make_user, monkeypatch):
    import handlers.onboarding as onboarding
    monkeypatch.setattr(onboarding, "get_db", lambda: db)
    user = make_user('801')
    ctx = _make_ctx(user)

    await onboarding.setkeys_command(_make_update('/setkeys'), ctx)
    assert ctx.user_data['state'] == 'awaiting_exchange_choice'

    await onboarding.handle_awaiting_exchange_choice(_make_update('BingX'), ctx)
    assert ctx.user_data['state'] == 'awaiting_bingx_key'
    assert ctx.user_data['pending_exchange'] == 'bingx'

    await onboarding.handle_awaiting_bingx_key(_make_update('API_KEY_1234567890'), ctx)
    assert ctx.user_data['state'] == 'awaiting_bingx_secret'

    with patch('handlers.onboarding.validate_keys', new=AsyncMock(return_value={'success': True, 'equity': 100.0})), \
         patch('services.history_import.import_trade_history', new=AsyncMock(return_value={
             'success': True, 'imported': 0, 'skipped': 0, 'total_found': 0
         })):
        await onboarding.handle_awaiting_bingx_secret(_make_update('SECRET_KEY_0987654321'), ctx)

    api_key, secret_key = db.get_bingx_keys(user['user_id'])
    assert api_key == 'API_KEY_1234567890'
    assert secret_key == 'SECRET_KEY_0987654321'
    assert db.get_user(user['user_id'])['exchange'] == 'bingx'


@pytest.mark.asyncio
async def test_setkeys_bybit_choice_stores_exchange(db, make_user, monkeypatch):
    """Задача от 13.07.2026 ("мультибиржевость обязательна") — выбор
    небазовой биржи на экране /setkeys реально сохраняется в users.exchange
    (core/user_context.py читает её при резолве адаптера на следующий
    апдейт)."""
    import handlers.onboarding as onboarding
    monkeypatch.setattr(onboarding, "get_db", lambda: db)
    user = make_user('803')
    ctx = _make_ctx(user)

    await onboarding.setkeys_command(_make_update('/setkeys'), ctx)
    await onboarding.handle_awaiting_exchange_choice(_make_update('Bybit'), ctx)
    assert ctx.user_data['pending_exchange'] == 'bybit'

    await onboarding.handle_awaiting_bingx_key(_make_update('BYBIT_KEY_1234567890'), ctx)

    with patch('handlers.onboarding.validate_keys', new=AsyncMock(return_value={'success': True, 'equity': 50.0})) as validate_mock, \
         patch('services.history_import.import_trade_history', new=AsyncMock(return_value={
             'success': True, 'imported': 0, 'skipped': 0, 'total_found': 0
         })):
        await onboarding.handle_awaiting_bingx_secret(_make_update('BYBIT_SECRET_0987654321'), ctx)

    validate_mock.assert_awaited_once_with('bybit', 'BYBIT_KEY_1234567890', 'BYBIT_SECRET_0987654321')
    assert db.get_user(user['user_id'])['exchange'] == 'bybit'


@pytest.mark.asyncio
async def test_setkeys_unrecognized_exchange_choice_reprompts():
    import handlers.onboarding as onboarding
    ctx = MagicMock()
    ctx.user_data = {'state': 'awaiting_exchange_choice'}
    await onboarding.handle_awaiting_exchange_choice(_make_update('Не биржа'), ctx)
    assert ctx.user_data['state'] == 'awaiting_exchange_choice'
    assert 'pending_exchange' not in ctx.user_data


@pytest.mark.asyncio
async def test_setkeys_invalid_key_not_stored(db, make_user, monkeypatch):
    import handlers.onboarding as onboarding
    monkeypatch.setattr(onboarding, "get_db", lambda: db)
    user = make_user('802')
    ctx = _make_ctx(user)

    await onboarding.setkeys_command(_make_update('/setkeys'), ctx)
    await onboarding.handle_awaiting_exchange_choice(_make_update('BingX'), ctx)
    await onboarding.handle_awaiting_bingx_key(_make_update('BAD_KEY_1234567890'), ctx)

    with patch('handlers.onboarding.validate_keys', new=AsyncMock(return_value={
        'success': False, 'error': 'invalid signature'
    })):
        await onboarding.handle_awaiting_bingx_secret(_make_update('BAD_SECRET_0987654321'), ctx)

    api_key, secret_key = db.get_bingx_keys(user['user_id'])
    assert api_key is None or api_key == ''


@pytest.mark.asyncio
async def test_setkeys_short_input_rejected_without_hitting_exchange():
    import handlers.onboarding as onboarding
    ctx = MagicMock()
    ctx.user_data = {'state': 'awaiting_bingx_key'}
    with patch('handlers.onboarding.validate_keys', new=AsyncMock()) as validate_mock:
        await onboarding.handle_awaiting_bingx_key(_make_update('short'), ctx)
    validate_mock.assert_not_called()
    assert ctx.user_data['state'] == 'awaiting_bingx_key'
