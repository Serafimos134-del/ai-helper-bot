"""
tests/e2e/test_full_journey.py
End-to-End: полный путь пользователя (задача от 12.07.2026, 9 шагов):
1. Старт бота.
2. Регистрация пользователя.
3. Запуск Trial.
4. Подключение биржи.
5. Получение аналитики.
6. Получение уведомлений.
7. Завершение Trial.
8. Оплата подписки.
9. Продление доступа.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(text=None, uid='2001', username='e2euser'):
    u = MagicMock()
    u.effective_user.id = int(uid)
    u.effective_user.username = username
    u.effective_chat.id = int(uid)
    u.effective_chat.send_message = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    u.callback_query = None
    u.message = MagicMock()
    u.message.text = text
    u.message.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock(), delete=AsyncMock()))
    u.message.delete = AsyncMock()
    return u


def _make_ctx():
    c = MagicMock()
    c.user_data = {}
    c.bot = MagicMock()
    c.bot.send_message = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_full_user_journey(db, monkeypatch):
    import core.user_context as uc
    import handlers.onboarding as onboarding
    import handlers.risk_profile as rp
    import handlers.subscription as sub
    import handlers.system as sysmod
    import core.scheduler as sched

    for mod in (uc, onboarding, rp, sub, sysmod):
        monkeypatch.setattr(mod, "get_db", lambda: db)

    ctx = _make_ctx()

    # 1-3. Старт бота -> регистрация -> Trial запускается автоматически
    await uc.resolve_user_context(_make_update(), ctx)
    assert ctx.user_data['is_authorized'] is True, "Trial should authorize immediately"
    user_id = uc.get_current_user_id(ctx)
    await sysmod.start(_make_update(), ctx)

    # 4. Подключение биржи (/setkeys)
    await onboarding.setkeys_command(_make_update('/setkeys'), ctx)
    await onboarding.handle_awaiting_bingx_key(_make_update('E2E_API_KEY_123456'), ctx)
    with patch('handlers.onboarding.validate_keys', new=AsyncMock(return_value={'success': True, 'equity': 300.0})), \
         patch('services.history_import.import_trade_history', new=AsyncMock(return_value={
             'success': True, 'imported': 0, 'skipped': 0, 'total_found': 0
         })):
        await onboarding.handle_awaiting_bingx_secret(_make_update('E2E_SECRET_KEY_654321'), ctx)
    api_key, secret_key = db.get_bingx_keys(user_id)
    assert api_key == 'E2E_API_KEY_123456'

    # 5. Получение аналитики (риск-профиль -> Risk Score)
    await rp.riskprofile_command(_make_update('/riskprofile'), ctx)
    await rp.handle_awaiting_risk_level(_make_update('⚖️ Сбалансированный'), ctx)
    await rp.handle_awaiting_trading_style(_make_update('📅 Внутри дня'), ctx)
    await rp.handle_awaiting_experience_level(_make_update('📊 Средний уровень'), ctx)
    await rp.handle_awaiting_risk_goal(_make_update('📈 Стабильный рост'), ctx)
    assert db.get_risk_profile(user_id)['risk_level'] == 'balanced'

    for i in range(6):
        db.add_closed_trade({
            'orderId': f'E2EJ{i}', 'symbol': 'ETH-USDT', 'side': 'LONG',
            'entry_price': 3000, 'exit_price': 3050, 'quantity': 1,
            'realized_pnl': 50, 'leverage': 5, 'stop_loss': 2900, 'take_profit': 3100,
            'dca_count': 0, 'user_id': user_id, 'close_time': f'2026-06-{i + 1:02d}T00:00:00',
        })
    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 300.0})):
        await rp.riskscore_command(_make_update('/riskscore'), ctx)
    assert db.get_risk_profile(user_id)['risk_score'] is not None

    # 6. Получение уведомлений (ежедневный отчёт)
    sched_ctx = _make_ctx()
    with patch('core.scheduler.set_current_exchange'), \
         patch('core.scheduler.clear_current_exchange'), \
         patch('core.scheduler.get_balance', new=AsyncMock(return_value={
             'success': True, 'equity': 300.0, 'available': 250.0, 'used_margin': 50.0, 'unrealized_pnl': 0.0
         })):
        await sched.daily_report_job(sched_ctx, db)
    sched_ctx.bot.send_message.assert_called_once()

    # 7. Завершение Trial -> доступ пропадает
    db.set_subscription(user_id, 'premium', '2020-01-01T00:00:00')
    ctx_after_trial = _make_ctx()
    await uc.resolve_user_context(_make_update(), ctx_after_trial)
    assert ctx_after_trial.user_data['is_authorized'] is False

    # 8. Оплата подписки
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    with patch('handlers.subscription.create_invoice', new=AsyncMock(return_value={
        'success': True, 'invoice_id': 'E2EJ_INV', 'pay_url': 'https://t.me/pay/e2ej'
    })):
        await sub.handle_plan_selected(query, ctx_after_trial, '30d')
    assert len(db.get_pending_payments()) == 1

    with patch('core.scheduler.get_invoice_statuses', new=AsyncMock(return_value={'E2EJ_INV': 'paid'})):
        await sched.crypto_pay_poll_job(sched_ctx, db)

    # 9. Продление доступа -> снова авторизован
    ctx_after_payment = _make_ctx()
    await uc.resolve_user_context(_make_update(), ctx_after_payment)
    assert ctx_after_payment.user_data['is_authorized'] is True
    assert db.is_premium(user_id) is True
