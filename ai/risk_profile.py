"""
ai/risk_profile.py
Персональная модель риска (задача от 12.07.2026) — НЕ отдельный риск-модуль
с нуля: каждый фактор Risk Score переиспользует уже существующие источники
данных, ничего не пересчитывается заново.

Risk Score 0-100 (выше = рискованнее), взвешенная сумма 6 факторов:

  leverage              20%  services.performance_engine.PerformanceEngine._leverage_analysis
                              (avg_leverage по последним закрытым сделкам)
  position_size         20%  текущий снепшот открытых позиций vs баланс (номинал/equity).
                              Исторический размер позиции к депозиту не восстановить —
                              closed_trades.risk_percent всегда 0 (см. TRADER_DNA_V1.md,
                              §1.1 — известный, задокументированный пробел, не входит в
                              рамки этой задачи), поэтому берём текущее состояние вместо
                              недоступного исторического среднего.
  drawdown               20%  максимальная просадка по кривой реализованного PnL
                              (peak-to-trough), в % от текущего баланса — честнее, чем
                              ai.risk_engine.RiskRuleEngine.assess()'s drawdown_pct (тот
                              всегда 0 без открытых позиций — интрадей unrealized-PnL,
                              не история).
  stop_loss_discipline  15%  ai.trader_dna.sl_tp_discipline() — доля сделок с SL+TP
  overtrading            15%  behavior_events (event_type='overtrading', BehaviorEngine),
                              частота за последние 30 дней
  dca_behavior           10%  closed_trades.dca_count (TradeManager.add_dca) — средняя
                              частота усреднений

Risk Score не персистится на каждый вызов AI Core — считается по команде
/riskscore (handlers/risk_profile.py) и кэшируется в user_risk_profile
(services/database.py:save_risk_score); ai/context_builder.py читает этот
снимок синхронно, не гоняет живой пересчёт (с обращением к балансу биржи)
на каждый /consilium.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from ai.trader_dna import sl_tp_discipline
from services.performance_engine import PerformanceEngine

logger = logging.getLogger(__name__)

RISK_SCORE_WEIGHTS = {
    'leverage': 0.20,
    'position_size': 0.20,
    'drawdown': 0.20,
    'stop_loss_discipline': 0.15,
    'overtrading': 0.15,
    'dca_behavior': 0.10,
}

MIN_TRADES_FOR_SCORE = 5
FULL_CONFIDENCE_TRADES = 20
OVERTRADING_LOOKBACK_DAYS = 30

RISK_LABEL_BUCKETS = (
    (30, 'низкий'),
    (60, 'средний'),
    (80, 'повышенный'),
    (101, 'критический'),
)

# Верхняя граница "нормального" Risk Score для каждого заявленного профиля
# (п.4 ТЗ: User Profile Risk VS Actual Trading Risk). Выше границы —
# фактический риск превышает заявленный.
DECLARED_LEVEL_CEILING = {
    'conservative': 40,
    'balanced': 65,
    'aggressive': 100,
}
DECLARED_LEVEL_LABELS = {
    'conservative': 'консервативный',
    'balanced': 'сбалансированный',
    'aggressive': 'агрессивный',
}


def _label(score: int) -> str:
    for threshold, name in RISK_LABEL_BUCKETS:
        if score < threshold:
            return name
    return 'критический'


def _leverage_component(avg_leverage: float) -> int:
    if not avg_leverage or avg_leverage <= 0:
        return 0
    if avg_leverage < 3:
        return 15
    if avg_leverage < 5:
        return 35
    if avg_leverage < 10:
        return 60
    if avg_leverage < 20:
        return 85
    return 100


def _position_size_component(open_trades: list, balance: float) -> tuple:
    if not open_trades or not balance or balance <= 0:
        return 0, None
    notional = sum(
        float(t.get('entry_price', 0)) * abs(float(t.get('quantity', 0)))
        for t in open_trades
    )
    exposure_pct = (notional / balance) * 100
    if exposure_pct < 20:
        score = 10
    elif exposure_pct < 50:
        score = 30
    elif exposure_pct < 100:
        score = 55
    elif exposure_pct < 150:
        score = 80
    else:
        score = 100
    return score, round(exposure_pct, 1)


def _drawdown_component(closed_trades: list, balance: float) -> tuple:
    if not closed_trades or not balance or balance <= 0:
        return 0, None
    ordered = sorted(closed_trades, key=lambda t: t.get('close_time') or '')
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in ordered:
        cum += float(t.get('realized_pnl', 0) or 0)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    dd_pct = (max_dd / balance) * 100
    if dd_pct < 5:
        score = 10
    elif dd_pct < 10:
        score = 35
    elif dd_pct < 20:
        score = 60
    elif dd_pct < 35:
        score = 85
    else:
        score = 100
    return score, round(dd_pct, 1)


def _overtrading_component(db, user_id: str) -> tuple:
    since = (datetime.now(timezone.utc) - timedelta(days=OVERTRADING_LOOKBACK_DAYS)).isoformat()
    events = db.get_recent_behavior_events(user_id, event_type='overtrading', limit=200)
    count = sum(1 for e in events if (e.get('created_at') or '') >= since)
    if count == 0:
        score = 5
    elif count <= 2:
        score = 30
    elif count <= 5:
        score = 60
    elif count <= 10:
        score = 85
    else:
        score = 100
    return score, count


def _dca_component(closed_trades: list) -> tuple:
    counts = [int(t.get('dca_count') or 0) for t in closed_trades]
    if not counts:
        return 0, None
    avg = sum(counts) / len(counts)
    if avg < 0.3:
        score = 10
    elif avg < 0.8:
        score = 35
    elif avg < 1.5:
        score = 60
    elif avg < 2.5:
        score = 85
    else:
        score = 100
    return score, round(avg, 2)


def _stop_discipline_component(closed_trades: list) -> tuple:
    discipline = sl_tp_discipline(closed_trades)
    if not discipline:
        return 0, None
    score = max(0, round(100 - discipline['rate']))
    return score, discipline['rate']


async def compute_risk_score(db, user_id: str) -> dict:
    """Живой пересчёт (нужен текущий баланс с биржи) — вызывать явно
    (/riskscore), не на каждый AI-запрос. Требует, чтобы биржа/ключи
    пользователя уже были установлены в contextvar (services/exchange_api.py)
    вызывающим кодом — как любой другой authenticated-вызов.

    Все синхронные вызовы БД — через asyncio.to_thread (тот же паттерн,
    что везде в core/scheduler.py): без этого они блокируют event loop
    целиком на время выполнения, включая await get_balance() ниже и
    обработку ВСЕХ остальных апдейтов бота, пока идёт запрос — при
    случайной коллизии с фоновой джобой, держащей self.lock БД
    (core/scheduler.py), это ощущается как зависание бота."""
    from services.exchange_api import get_balance

    closed_trades = await asyncio.to_thread(db.get_closed_trades, limit=100, user_id=user_id)
    if len(closed_trades) < MIN_TRADES_FOR_SCORE:
        return {
            'score': None, 'label': None, 'confidence': 'insufficient_data',
            'components': {}, 'details': {}, 'total_trades': len(closed_trades),
        }

    perf = PerformanceEngine(db)
    report = await asyncio.to_thread(perf.get_full_report, user_id=user_id)
    open_trades = await asyncio.to_thread(db.get_open_trades, user_id=user_id)

    # get_balance() уже сам ограничен по времени (httpx timeout=10s x до
    # 3 попыток внутри _request_with_retry, см. services/bingx_api.py) —
    # но это до ~30 секунд суммарно без обратной связи пользователю, что
    # на практике воспринимается как "подвис". Явный внешний таймаут даёт
    # быстрый и понятный ответ вместо тишины.
    try:
        balance_result = await asyncio.wait_for(get_balance(), timeout=15)
    except asyncio.TimeoutError:
        balance_result = {'success': False, 'error': 'таймаут запроса баланса к бирже'}
    balance = balance_result.get('equity', 0) if balance_result.get('success') else 0

    leverage_score = _leverage_component(report['leverage']['avg_leverage'])
    position_score, position_detail = _position_size_component(open_trades, balance)
    drawdown_score, drawdown_detail = _drawdown_component(closed_trades, balance)
    discipline_score, discipline_detail = _stop_discipline_component(closed_trades)
    overtrading_score, overtrading_detail = await asyncio.to_thread(_overtrading_component, db, user_id)
    dca_score, dca_detail = _dca_component(closed_trades)

    components = {
        'leverage': leverage_score,
        'position_size': position_score,
        'drawdown': drawdown_score,
        'stop_loss_discipline': discipline_score,
        'overtrading': overtrading_score,
        'dca_behavior': dca_score,
    }
    score = round(sum(components[k] * RISK_SCORE_WEIGHTS[k] for k in RISK_SCORE_WEIGHTS))

    return {
        'score': score,
        'label': _label(score),
        'confidence': 'full' if len(closed_trades) >= FULL_CONFIDENCE_TRADES else 'low',
        'components': components,
        'details': {
            'avg_leverage': report['leverage']['avg_leverage'],
            'position_exposure_pct': position_detail,
            'max_drawdown_pct': drawdown_detail,
            'stop_loss_discipline_rate': discipline_detail,
            'overtrading_events_30d': overtrading_detail,
            'avg_dca_count': dca_detail,
        },
        'total_trades': len(closed_trades),
    }


def compare_declared_vs_actual(declared_level: str, risk_score_result: dict) -> dict:
    """User Profile Risk VS Actual Trading Risk (п.4 ТЗ) — не блокирует,
    только объясняет расхождение (п.5: RiskEngine не запрещает действия)."""
    score = risk_score_result.get('score')
    if score is None or not declared_level:
        return {'mismatch': False, 'text': None}

    ceiling = DECLARED_LEVEL_CEILING.get(declared_level, 100)
    if score <= ceiling:
        return {'mismatch': False, 'text': None}

    excess = score - ceiling
    declared_ru = DECLARED_LEVEL_LABELS.get(declared_level, declared_level)
    text = (
        f"⚠️ Ваш фактический уровень риска ({score}/100, {risk_score_result['label']}) "
        f"выше выбранного профиля «{declared_ru}» примерно на {excess} пунктов."
    )
    comp = risk_score_result.get('components', {})
    details = risk_score_result.get('details', {})
    reasons = []
    if comp.get('leverage', 0) >= 60:
        reasons.append(f"повышенное плечо (в среднем {details.get('avg_leverage')}x)")
    if comp.get('drawdown', 0) >= 60:
        reasons.append(f"просадка до {details.get('max_drawdown_pct')}% от депозита")
    if comp.get('position_size', 0) >= 60:
        reasons.append(f"крупный размер позиций ({details.get('position_exposure_pct')}% от депозита)")
    if comp.get('dca_behavior', 0) >= 60:
        reasons.append(f"частые усреднения (в среднем {details.get('avg_dca_count')} на сделку)")
    if comp.get('overtrading', 0) >= 60:
        reasons.append("частая переторговля")
    if comp.get('stop_loss_discipline', 0) >= 60:
        reasons.append(f"стопы выставлены только в {details.get('stop_loss_discipline_rate')}% сделок")
    if reasons:
        text += "\nОсновные причины: " + "; ".join(reasons) + "."
    return {'mismatch': True, 'text': text, 'excess': excess}


def build_risk_profile_context(db, user_id: str) -> dict:
    """Синхронное чтение последнего посчитанного профиля/скора для
    ai/context_builder.py — тот же паттерн, что ai/trader_context.py:
    build_trader_context (собирается в asyncio.to_thread рядом с ним)."""
    profile = db.get_risk_profile(user_id)
    if not profile:
        return None
    import json as _json
    components = {}
    if profile.get('risk_score_components'):
        try:
            components = _json.loads(profile['risk_score_components'])
        except (ValueError, TypeError):
            components = {}
    return {
        'declared': {
            'risk_level': profile.get('risk_level'),
            'trading_style': profile.get('trading_style'),
            'experience_level': profile.get('experience_level'),
            'risk_goal': profile.get('risk_goal'),
        },
        'risk_score': {
            'score': profile.get('risk_score'),
            'label': _label(profile['risk_score']) if profile.get('risk_score') is not None else None,
            'components': components,
        },
    }


def compute_risk_profile_adjustment(risk_profile_ctx: dict) -> dict:
    """Advisory-поправка к финальному счёту JudgeAgent — тот же паттерн,
    что ai/trader_context.py:compute_dna_adjustment (capped delta,
    'active' флаг, не блокирует). Высокий фактический Risk Score немного
    снижает итоговый score консилиума (п.5 ТЗ: рекомендации, не запреты —
    поэтому поправка ограничена, а не хард-стоп)."""
    if not risk_profile_ctx:
        return {'score_delta': 0, 'reason': None, 'active': False}
    score = (risk_profile_ctx.get('risk_score') or {}).get('score')
    if score is None or score < 60:
        return {'score_delta': 0, 'reason': None, 'active': False}
    delta = -min(10, round((score - 60) / 4))
    if delta == 0:
        return {'score_delta': 0, 'reason': None, 'active': False}
    label = (risk_profile_ctx.get('risk_score') or {}).get('label', '')
    reason = f"Фактический Risk Score пользователя {score}/100 ({label}) — повышенная рискованность поведения учтена."
    return {'score_delta': delta, 'reason': reason, 'active': True}
