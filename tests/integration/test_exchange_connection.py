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
    assert ctx.user_data['state'] == 'awaiting_bingx_key'

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


@pytest.mark.asyncio
async def test_setkeys_invalid_key_not_stored(db, make_user, monkeypatch):
    import handlers.onboarding as onboarding
    monkeypatch.setattr(onboarding, "get_db", lambda: db)
    user = make_user('802')
    ctx = _make_ctx(user)

    await onboarding.setkeys_command(_make_update('/setkeys'), ctx)
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
