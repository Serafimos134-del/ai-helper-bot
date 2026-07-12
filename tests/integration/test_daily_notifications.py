"""
tests/integration/test_daily_notifications.py
Интеграционный тест: ежедневные уведомления (core/scheduler.py:daily_report_job)
— изоляция между пользователями, защита от дублей, отключаемость.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_daily_report_sent_only_to_eligible_subscriber(db, make_user, owner_user):
    import core.scheduler as sched

    alice = make_user('1101', 'alice')
    db.set_bingx_keys(alice['user_id'], 'A_KEY', 'A_SECRET')

    bob = make_user('1102', 'bob')  # no keys -> excluded
    charlie = make_user('1103', 'charlie')  # keys but expired subscription -> excluded
    db.set_bingx_keys(charlie['user_id'], 'C_KEY', 'C_SECRET')
    db.set_subscription(charlie['user_id'], 'premium', '2020-01-01T00:00:00')

    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()

    with patch('core.scheduler.set_current_exchange'), \
         patch('core.scheduler.clear_current_exchange'), \
         patch('core.scheduler.get_balance', new=AsyncMock(return_value={
             'success': True, 'equity': 100.0, 'available': 90.0, 'used_margin': 10.0, 'unrealized_pnl': 0.0
         })):
        await sched.daily_report_job(ctx, db)

    assert ctx.bot.send_message.call_count == 1
    assert ctx.bot.send_message.call_args.kwargs['chat_id'] == int(alice['telegram_id'])


@pytest.mark.asyncio
async def test_daily_report_not_sent_twice_same_day(db, make_user):
    import core.scheduler as sched
    alice = make_user('1104', 'alice')
    db.set_bingx_keys(alice['user_id'], 'A_KEY', 'A_SECRET')

    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()

    with patch('core.scheduler.set_current_exchange'), \
         patch('core.scheduler.clear_current_exchange'), \
         patch('core.scheduler.get_balance', new=AsyncMock(return_value={
             'success': True, 'equity': 100.0, 'available': 90.0, 'used_margin': 10.0, 'unrealized_pnl': 0.0
         })):
        await sched.daily_report_job(ctx, db)
        await sched.daily_report_job(ctx, db)  # simulate a second tick same day

    assert ctx.bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_daily_report_respects_notifications_disabled(db, make_user):
    import core.scheduler as sched
    alice = make_user('1105', 'alice')
    db.set_bingx_keys(alice['user_id'], 'A_KEY', 'A_SECRET')
    db.set_notifications_enabled(alice['user_id'], False)

    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    await sched.daily_report_job(ctx, db)

    ctx.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_daily_report_one_users_failure_does_not_block_others(db, make_user):
    """Один упавший пользователь (например, заблокировал бота) не должен
    остановить рассылку остальным."""
    import core.scheduler as sched
    alice = make_user('1106', 'alice')
    db.set_bingx_keys(alice['user_id'], 'A_KEY', 'A_SECRET')
    bob = make_user('1107', 'bob')
    db.set_bingx_keys(bob['user_id'], 'B_KEY', 'B_SECRET')

    ctx = MagicMock()
    ctx.bot = MagicMock()

    async def send_message(chat_id, **kwargs):
        if chat_id == int(alice['telegram_id']):
            raise RuntimeError("bot was blocked by this user")
        return MagicMock()

    ctx.bot.send_message = AsyncMock(side_effect=send_message)

    with patch('core.scheduler.set_current_exchange'), \
         patch('core.scheduler.clear_current_exchange'), \
         patch('core.scheduler.get_balance', new=AsyncMock(return_value={
             'success': True, 'equity': 100.0, 'available': 90.0, 'used_margin': 10.0, 'unrealized_pnl': 0.0
         })):
        await sched.daily_report_job(ctx, db)

    assert ctx.bot.send_message.call_count == 2
