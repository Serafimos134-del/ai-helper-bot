"""
tests/unit/test_risk_engine.py
Unit-тесты Risk Engine (ai/risk_profile.py) — Risk Score, сравнение
заявленного и фактического риска, advisory-поправка для JudgeAgent.
"""

from unittest.mock import AsyncMock, patch

import pytest


def _add_trades(db, user_id, n=6, leverage=25, dca_count=2, stop_loss=None,
                 take_profit=None, pnl=50):
    for i in range(n):
        db.add_closed_trade({
            'orderId': f'RE{user_id}{i}', 'symbol': 'BTC-USDT', 'side': 'LONG',
            'entry_price': 100, 'exit_price': 101, 'quantity': 1,
            'realized_pnl': pnl, 'leverage': leverage,
            'stop_loss': stop_loss, 'take_profit': take_profit,
            'dca_count': dca_count, 'user_id': user_id,
            'close_time': f'2026-06-{i + 1:02d}T00:00:00',
        })


@pytest.mark.asyncio
async def test_insufficient_data_below_min_trades(db, make_user):
    from ai.risk_profile import compute_risk_score, MIN_TRADES_FOR_SCORE
    user = make_user('501')
    _add_trades(db, user['user_id'], n=MIN_TRADES_FOR_SCORE - 1)
    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 1000})):
        result = await compute_risk_score(db, user['user_id'])
    assert result['score'] is None
    assert result['confidence'] == 'insufficient_data'


@pytest.mark.asyncio
async def test_high_risk_trader_scores_high(db, make_user):
    from ai.risk_profile import compute_risk_score
    user = make_user('502')
    _add_trades(db, user['user_id'], n=10, leverage=25, dca_count=2, stop_loss=None, take_profit=None)
    db.add_open_trade({
        'orderId': 'RE502_OPEN', 'symbol': 'BTC-USDT', 'side': 'LONG',
        'entry_price': 50000, 'quantity': 0.03, 'leverage': 25, 'user_id': user['user_id'],
    })
    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 1000})):
        result = await compute_risk_score(db, user['user_id'])
    assert result['score'] is not None
    assert result['score'] >= 60
    assert result['components']['leverage'] == 100
    assert result['components']['stop_loss_discipline'] == 100  # no SL/TP -> max risk


@pytest.mark.asyncio
async def test_disciplined_trader_scores_low(db, make_user):
    from ai.risk_profile import compute_risk_score
    user = make_user('503')
    _add_trades(db, user['user_id'], n=10, leverage=3, dca_count=0, stop_loss=95, take_profit=110)
    with patch('services.bingx_api.get_balance', new=AsyncMock(return_value={'success': True, 'equity': 1000})):
        result = await compute_risk_score(db, user['user_id'])
    assert result['score'] is not None
    assert result['score'] <= 30
    assert result['components']['stop_loss_discipline'] == 0  # full discipline -> 0 risk


def test_compare_declared_vs_actual_flags_mismatch():
    from ai.risk_profile import compare_declared_vs_actual
    high_risk = {'score': 85, 'label': 'критический', 'components': {'leverage': 100}, 'details': {'avg_leverage': 25}}
    cmp = compare_declared_vs_actual('conservative', high_risk)
    assert cmp['mismatch'] is True
    assert 'выше выбранного профиля' in cmp['text']


def test_compare_declared_vs_actual_no_mismatch_when_aligned():
    from ai.risk_profile import compare_declared_vs_actual
    low_risk = {'score': 20, 'label': 'низкий', 'components': {}, 'details': {}}
    cmp = compare_declared_vs_actual('conservative', low_risk)
    assert cmp['mismatch'] is False


def test_compare_declared_vs_actual_no_declared_profile():
    from ai.risk_profile import compare_declared_vs_actual
    cmp = compare_declared_vs_actual(None, {'score': 90, 'label': 'критический'})
    assert cmp['mismatch'] is False


def test_risk_profile_adjustment_active_for_high_score():
    from ai.risk_profile import compute_risk_profile_adjustment
    adj = compute_risk_profile_adjustment({'risk_score': {'score': 90, 'label': 'критический'}})
    assert adj['active'] is True
    assert adj['score_delta'] < 0
    assert adj['score_delta'] >= -10  # capped


def test_risk_profile_adjustment_inactive_for_low_score():
    from ai.risk_profile import compute_risk_profile_adjustment
    adj = compute_risk_profile_adjustment({'risk_score': {'score': 20, 'label': 'низкий'}})
    assert adj['active'] is False
    assert adj['score_delta'] == 0


def test_risk_profile_adjustment_inactive_for_none():
    from ai.risk_profile import compute_risk_profile_adjustment
    assert compute_risk_profile_adjustment(None) == {'score_delta': 0, 'reason': None, 'active': False}


@pytest.mark.asyncio
async def test_judge_agent_applies_risk_profile_adjustment():
    import json
    from ai.agents.judge_agent import JudgeAgent
    judge = JudgeAgent()
    market_json = json.dumps({'market_score': 70})
    risk_json = json.dumps({'risk_score': 70})
    psych_json = json.dumps({'psychology_score': 70})

    no_rp = json.loads(await judge.synthesize(market_json, risk_json, psych_json, mode='setup'))
    with_rp = json.loads(await judge.synthesize(
        market_json, risk_json, psych_json, mode='setup',
        risk_profile={'risk_score': {'score': 90, 'label': 'критический'}}
    ))
    assert no_rp['risk_profile_adjustment']['active'] is False
    assert with_rp['risk_profile_adjustment']['active'] is True
    assert with_rp['final_score'] < no_rp['final_score']
