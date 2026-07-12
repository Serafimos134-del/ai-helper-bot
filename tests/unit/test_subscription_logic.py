"""
tests/unit/test_subscription_logic.py
Unit-тесты подписки: тарифы, продление, идемпотентность оплаты,
брошенные/просроченные счета.
"""

import pytest


def test_subscription_plans_match_agreed_pricing():
    from core.billing import SUBSCRIPTION_PLANS
    assert SUBSCRIPTION_PLANS['14d'] == {'days': 14, 'price': 4, 'label': '14 дней'}
    assert SUBSCRIPTION_PLANS['30d'] == {'days': 30, 'price': 8, 'label': '1 месяц'}
    assert SUBSCRIPTION_PLANS['180d'] == {'days': 180, 'price': 35, 'label': '6 месяцев'}


def test_new_user_gets_automatic_trial(db, make_user):
    from core.billing import TRIAL_PERIOD_DAYS
    from datetime import datetime, timedelta
    user = make_user('601')
    assert user['subscription_tier'] == 'premium'
    assert db.is_premium(user['user_id']) is True
    expiry = datetime.fromisoformat(user['subscription_expires_at'])
    assert expiry > datetime.now() + timedelta(days=TRIAL_PERIOD_DAYS - 1)


def test_expired_subscription_is_not_premium(db, make_user):
    user = make_user('602')
    db.set_subscription(user['user_id'], 'premium', '2020-01-01T00:00:00')
    assert db.is_premium(user['user_id']) is False


def test_extend_subscription_from_future_expiry_does_not_lose_remainder(db, make_user):
    from datetime import datetime, timedelta
    user = make_user('603')
    future = (datetime.now() + timedelta(days=10)).isoformat()
    db.set_subscription(user['user_id'], 'premium', future)
    new_expiry = db.extend_subscription(user['user_id'], 14)
    assert datetime.fromisoformat(new_expiry) > datetime.now() + timedelta(days=23)


def test_extend_subscription_from_expired_starts_from_now(db, make_user):
    from datetime import datetime, timedelta
    user = make_user('604')
    db.set_subscription(user['user_id'], 'premium', '2020-01-01T00:00:00')
    new_expiry = db.extend_subscription(user['user_id'], 14)
    expiry_dt = datetime.fromisoformat(new_expiry)
    assert timedelta(days=13) < (expiry_dt - datetime.now()) < timedelta(days=15)


def test_payment_lifecycle_create_pay_idempotent(db, make_user):
    user = make_user('605')
    db.create_payment('INV1', user['user_id'], 8, 'USDT', days=30)
    assert len(db.get_pending_payments()) == 1

    credited = db.mark_payment_paid('INV1')
    assert credited is not None
    assert credited['days'] == 30

    credited_again = db.mark_payment_paid('INV1')
    assert credited_again is None, "double credit on repeated poll"
    assert db.get_pending_payments() == []


def test_mark_payment_expired_stops_polling(db, make_user):
    user = make_user('606')
    db.create_payment('INV2', user['user_id'], 4, 'USDT', days=14)
    db.mark_payment_expired('INV2')
    assert db.get_pending_payments() == []
    row = db._execute("SELECT status FROM payments WHERE invoice_id = 'INV2'").fetchone()
    assert row['status'] == 'expired'


def test_mark_payment_expired_does_not_touch_paid(db, make_user):
    user = make_user('607')
    db.create_payment('INV3', user['user_id'], 4, 'USDT', days=14)
    db.mark_payment_paid('INV3')
    db.mark_payment_expired('INV3')  # should be a no-op
    row = db._execute("SELECT status FROM payments WHERE invoice_id = 'INV3'").fetchone()
    assert row['status'] == 'paid'
