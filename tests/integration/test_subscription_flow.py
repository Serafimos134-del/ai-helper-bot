"""
tests/integration/test_subscription_flow.py
Интеграционный тест: полный цикл подписки — /subscribe -> выбор тарифа ->
счёт Crypto Pay -> подтверждение оплаты (crypto_pay_poll_job) -> продление.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_subscribe_and_pay_extends_subscription(db, make_user, monkeypatch):
    import handlers.subscription as sub
    import core.scheduler as sched
    monkeypatch.setattr(sub, "get_db", lambda: db)

    user = make_user('1001')
    ctx = MagicMock()
    ctx.user_data = {'user': user}

    query = MagicMock()
    query.edit_message_text = AsyncMock()
    with patch('handlers.subscription.create_invoice', new=AsyncMock(return_value={
        'success': True, 'invoice_id': 'SUBFLOW_INV', 'pay_url': 'https://t.me/pay/x'
    })):
        await sub.handle_plan_selected(query, ctx, '14d')

    pending = db.get_pending_payments()
    assert len(pending) == 1
    assert pending[0]['days'] == 14
    assert pending[0]['amount'] == 4

    sched_ctx = MagicMock()
    sched_ctx.bot = MagicMock()
    sched_ctx.bot.send_message = AsyncMock()
    with patch('core.scheduler.get_invoice_statuses', new=AsyncMock(return_value={'SUBFLOW_INV': 'paid'})):
        await sched.crypto_pay_poll_job(sched_ctx, db)

    assert db.is_premium(user['user_id']) is True
    sched_ctx.bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_unpaid_invoice_expires_and_stops_being_polled(db, make_user):
    import core.scheduler as sched
    user = make_user('1002')  # new user already has a trial expiry ~14d out
    trial_expiry = user['subscription_expires_at']
    db.create_payment('SUBFLOW_ABANDONED', user['user_id'], 8, 'USDT', days=30)

    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    with patch('core.scheduler.get_invoice_statuses', new=AsyncMock(return_value={'SUBFLOW_ABANDONED': 'expired'})):
        await sched.crypto_pay_poll_job(ctx, db)

    assert db.get_pending_payments() == []
    # Expired invoice must NOT extend the subscription -- expiry stays at
    # the original trial date, not pushed 30 days further.
    unchanged_user = db.get_user(user['user_id'])
    assert unchanged_user['subscription_expires_at'] == trial_expiry
    ctx.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_plan_id_rejected():
    import handlers.subscription as sub
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    ctx = MagicMock()
    ctx.user_data = {'user': {'user_id': 'x'}}
    await sub.handle_plan_selected(query, ctx, 'not_a_real_plan')
    query.edit_message_text.assert_called_once()
    assert 'Неизвестный тариф' in query.edit_message_text.call_args.args[0]
