"""
tests/unit/test_access_control.py
Unit-тесты доступа: владелец/подписчик/неавторизован, require_auth, изоляция
данных между пользователями (user_id-скоуп в БД).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_owner_is_authorized_without_subscription(db, owner_user):
    import core.user_context as uc
    assert bool(uc._OWNER_CHAT_ID)
    # Owner row itself carries a premium trial too (get_or_create_user), but
    # is_owner should be independently true by telegram_id match.
    assert owner_user['telegram_id'] == uc._OWNER_CHAT_ID


def test_non_owner_without_subscription_is_not_authorized(db, make_user):
    user = make_user('701')
    db.set_subscription(user['user_id'], 'premium', '2020-01-01T00:00:00')
    assert db.is_premium(user['user_id']) is False


@pytest.mark.asyncio
async def test_require_auth_denies_and_replies_when_not_authorized():
    from core.user_context import require_auth
    update = MagicMock()
    update.callback_query = None
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.user_data = {'is_authorized': False}

    result = await require_auth(update, context)
    assert result is False
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_require_auth_allows_when_authorized():
    from core.user_context import require_auth
    update = MagicMock()
    context = MagicMock()
    context.user_data = {'is_authorized': True}
    assert await require_auth(update, context) is True


def test_get_current_user_id_defaults_when_no_user_resolved():
    from core.user_context import get_current_user_id
    context = MagicMock()
    context.user_data = {}
    assert get_current_user_id(context) == 'default'


def test_get_current_user_id_returns_resolved_user(make_user):
    from core.user_context import get_current_user_id
    user = make_user('702')
    context = MagicMock()
    context.user_data = {'user': user}
    assert get_current_user_id(context) == user['user_id']


def test_closed_trades_are_isolated_between_users(db, make_user):
    alice = make_user('703', 'alice')
    bob = make_user('704', 'bob')
    db.add_closed_trade({
        'orderId': 'A1', 'symbol': 'ETH-USDT', 'side': 'LONG',
        'entry_price': 100, 'exit_price': 110, 'quantity': 1,
        'realized_pnl': 10, 'user_id': alice['user_id'], 'close_time': '2026-06-01T00:00:00',
    })
    db.add_closed_trade({
        'orderId': 'B1', 'symbol': 'SOL-USDT', 'side': 'SHORT',
        'entry_price': 150, 'exit_price': 140, 'quantity': 1,
        'realized_pnl': 10, 'user_id': bob['user_id'], 'close_time': '2026-06-01T00:00:00',
    })
    alice_trades = db.get_closed_trades(user_id=alice['user_id'])
    bob_trades = db.get_closed_trades(user_id=bob['user_id'])
    assert [t['symbol'] for t in alice_trades] == ['ETH-USDT']
    assert [t['symbol'] for t in bob_trades] == ['SOL-USDT']


def test_find_trade_by_id_scoped_prevents_idor(db, make_user):
    """IDOR-защита (см. MULTITENANCY_MIGRATION_PLAN.md): trade_id — общий
    auto-increment на всех пользователей, user_id обязателен для чтения
    чужой сделки по подобранному числовому ID."""
    alice = make_user('705', 'alice')
    bob = make_user('706', 'bob')
    db.add_closed_trade({
        'orderId': 'A2', 'symbol': 'ETH-USDT', 'side': 'LONG',
        'entry_price': 100, 'exit_price': 110, 'quantity': 1,
        'realized_pnl': 10, 'user_id': alice['user_id'], 'close_time': '2026-06-01T00:00:00',
    })
    trade = db.get_closed_trades(user_id=alice['user_id'])[0]
    assert db.find_trade_by_id(trade['id'], user_id=bob['user_id']) is None
    assert db.find_trade_by_id(trade['id'], user_id=alice['user_id']) is not None
