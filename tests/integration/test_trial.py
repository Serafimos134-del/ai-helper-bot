"""
tests/integration/test_trial.py
Интеграционный тест: trial-период через resolve_user_context (реальный
путь входа нового Telegram-пользователя, не прямой вызов db-метода).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_telegram_update(telegram_id: str, username: str = "newuser"):
    u = MagicMock()
    u.effective_user.id = int(telegram_id)
    u.effective_user.username = username
    u.callback_query = None
    u.message = None
    return u


@pytest.mark.asyncio
async def test_brand_new_user_gets_trial_via_middleware(db, monkeypatch):
    import core.user_context as uc
    monkeypatch.setattr(uc, "get_db", lambda: db)

    context = MagicMock()
    context.user_data = {}

    await uc.resolve_user_context(_make_telegram_update('999001'), context)

    assert context.user_data['is_authorized'] is True
    assert context.user_data['is_owner'] is False
    assert context.user_data['user']['subscription_tier'] == 'premium'


@pytest.mark.asyncio
async def test_owner_authorized_regardless_of_subscription(db, monkeypatch):
    import core.user_context as uc
    monkeypatch.setattr(uc, "get_db", lambda: db)

    context = MagicMock()
    context.user_data = {}
    await uc.resolve_user_context(_make_telegram_update(uc._OWNER_CHAT_ID, 'owner'), context)

    assert context.user_data['is_owner'] is True
    assert context.user_data['is_authorized'] is True


@pytest.mark.asyncio
async def test_returning_user_after_trial_expiry_not_authorized(db, monkeypatch):
    import core.user_context as uc
    monkeypatch.setattr(uc, "get_db", lambda: db)

    user = db.get_or_create_user('999002', 'expiring')
    db.set_subscription(user['user_id'], 'premium', '2020-01-01T00:00:00')

    context = MagicMock()
    context.user_data = {}
    await uc.resolve_user_context(_make_telegram_update('999002', 'expiring'), context)

    assert context.user_data['is_authorized'] is False


@pytest.mark.asyncio
async def test_subscriber_without_own_keys_does_not_see_owners_balance(db, monkeypatch):
    """Регрессионный тест на реальный баг, найденный на живом тесте с
    другого Telegram-аккаунта: подписчик БЕЗ своих привязанных ключей не
    должен получать доступ к балансу владельца через .env-фолбэк. Раньше
    resolve_user_context ставил ключи владельца по умолчанию для любого
    пользователя без своих — теперь только явно для is_owner."""
    import core.user_context as uc
    from services.bingx_api import _get_credentials
    monkeypatch.setattr(uc, "get_db", lambda: db)

    context = MagicMock()
    context.user_data = {}
    await uc.resolve_user_context(_make_telegram_update('999003', 'freshsubscriber'), context)

    assert context.user_data['is_owner'] is False
    assert context.user_data['is_authorized'] is True  # trial, но без ключей биржи
    assert _get_credentials() == ('', ''), "subscriber without own keys must NOT inherit owner's .env credentials"


@pytest.mark.asyncio
async def test_owner_without_own_keys_gets_env_fallback(db, monkeypatch):
    import core.user_context as uc
    from services.bingx_api import _get_credentials
    monkeypatch.setattr(uc, "get_db", lambda: db)

    context = MagicMock()
    context.user_data = {}
    await uc.resolve_user_context(_make_telegram_update(uc._OWNER_CHAT_ID, 'owner'), context)

    assert context.user_data['is_owner'] is True
    assert _get_credentials() == ('GLOBAL_TEST_KEY', 'GLOBAL_TEST_SECRET')
