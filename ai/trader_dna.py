"""
ai/trader_dna.py
Trader DNA v1 (см. TRADER_DNA_V1.md) — детерминированные read-only функции
поверх уже существующих данных (closed_trades, behavior_events,
PerformanceEngine). Не новая таблица, не новый агент: тот же принцип
источника истины, что и ai/trader_context.py — считается заново по
запросу, ничего не персистирует.

Разделение ответственности: ai/trader_context.py — TraderContext, который
видит JudgeAgent (advisory-поправка к оценке сделки, только для решения).
Этот модуль — витрина Trader DNA для пользователя (каталог паттернов +
DNA Score), выводится по запросу, не влияет на решения консилиума.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from services.performance_engine import PerformanceEngine

logger = logging.getLogger(__name__)

# Пороги (TRADER_DNA_V1.md, §1.4: "не строгая статистика, но осознанные
# пороги" — та же философия, что MIN_TOTAL_TRADES/MIN_SYMBOL_TRADES в
# ai/trader_context.py).
MIN_TRADES_FOR_PATTERNS = 5
MIN_BUCKET_TRADES = 5           # минимум сделок в баке (символ/сессия) для Consistency
MIN_PATTERN_EVENTS = 3          # минимум срабатываний конкретного поведенческого
                                 # флага на символ, чтобы это стало "закономерностью",
                                 # а не единичным случаем
BEHAVIOR_JOIN_WINDOW_MINUTES = 15  # behavior_events не хранит order_id
                                    # (TRADER_DNA_V1.md §1.1) — джойн приближённый,
                                    # по ближайшему времени; при >1 совпадении
                                    # событие не засчитывается (неоднозначно)
HOLDING_OVEREXTEND_RATIO = 1.3
EARLY_EXIT_FRACTION = 0.5
STOP_WIDTH_DEVIATION = 0.5

DNA_SCORE_MIN_TRADES = 20
DNA_SCORE_FULL_CONFIDENCE_TRADES = 50
DNA_SCORE_WEIGHTS = {
    'edge': 0.35,
    'discipline': 0.25,
    'behavior_stability': 0.25,
    'consistency': 0.15,
}


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ═══════════════════════ Фаза 2: каталог паттернов ═══════════════════════

def market_regime_pattern(trades: list) -> dict:
    """Результат по рыночному режиму. Тривиальный GROUP BY по уже
    существующей колонке closed_trades.market_trend — не новые данные
    (TRADER_DNA_V1.md §1.2)."""
    buckets = {}
    for t in trades:
        trend = t.get('market_trend')
        if not trend:
            continue
        pnl = float(t.get('realized_pnl', 0))
        b = buckets.setdefault(trend, {'total': 0, 'wins': 0, 'pnl': 0.0})
        b['total'] += 1
        b['pnl'] += pnl
        if pnl > 0:
            b['wins'] += 1
    return {
        trend: {
            'total': b['total'],
            'winrate': round(b['wins'] / b['total'] * 100, 1),
            'pnl': round(b['pnl'], 2),
        }
        for trend, b in buckets.items() if b['total'] >= 2
    }


def holding_time_pattern(trades: list) -> Optional[dict]:
    """Переудержание убыточных позиций — держат ли их заметно дольше прибыльных."""
    wins = [float(t['holding_minutes']) for t in trades
            if t.get('holding_minutes') is not None and float(t['realized_pnl']) > 0]
    losses = [float(t['holding_minutes']) for t in trades
              if t.get('holding_minutes') is not None and float(t['realized_pnl']) < 0]
    if len(wins) < 2 or len(losses) < 2:
        return None
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    return {
        'avg_holding_win': round(avg_win, 1),
        'avg_holding_loss': round(avg_loss, 1),
        'overextends_losses': avg_loss > avg_win * HOLDING_OVEREXTEND_RATIO,
    }


def early_exit_pattern(trades: list) -> Optional[dict]:
    """Ранняя фиксация прибыли относительно выставленного take_profit."""
    relevant = [
        t for t in trades
        if float(t.get('realized_pnl', 0)) > 0 and t.get('take_profit')
        and float(t['take_profit']) != float(t['entry_price'])
    ]
    if len(relevant) < MIN_PATTERN_EVENTS:
        return None
    early = 0
    for t in relevant:
        entry = float(t['entry_price'])
        exit_ = float(t['exit_price'])
        tp = float(t['take_profit'])
        full_distance = abs(tp - entry)
        if full_distance == 0:
            continue
        covered = abs(exit_ - entry) / full_distance
        if covered < EARLY_EXIT_FRACTION:
            early += 1
    return {
        'trades_with_tp': len(relevant),
        'early_exits': early,
        'early_exit_rate': round(early / len(relevant) * 100, 1),
    }


def stop_width_pattern(trades: list) -> Optional[dict]:
    """Отклонение дистанции стопа от собственной средней нормы — не внешняя
    "правильность" (её тут неоткуда взять), а сравнение с самим собой."""
    distances = []
    for t in trades:
        sl = t.get('stop_loss')
        entry = float(t.get('entry_price', 0))
        if not sl or entry == 0:
            continue
        distances.append(abs(entry - float(sl)) / entry)
    if len(distances) < MIN_PATTERN_EVENTS:
        return None
    avg = sum(distances) / len(distances)
    if avg == 0:
        return None
    narrow = sum(1 for d in distances if d < avg * (1 - STOP_WIDTH_DEVIATION))
    wide = sum(1 for d in distances if d > avg * (1 + STOP_WIDTH_DEVIATION))
    return {
        'trades_with_sl': len(distances),
        'avg_stop_distance_pct': round(avg * 100, 2),
        'narrow_stop_count': narrow,
        'wide_stop_count': wide,
    }


def quality_after_loss_streak_pattern(trades: list, streak_threshold: int = 2) -> Optional[dict]:
    """Качество решений (ai_score) сразу после серии убытков vs базовое среднее."""
    scored = [t for t in trades if t.get('ai_score') is not None]
    if len(scored) < 10:
        return None
    baseline_avg = sum(t['ai_score'] for t in scored) / len(scored)

    after_streak_scores = []
    loss_streak = 0
    for t in sorted(trades, key=lambda t: t.get('close_time') or ''):
        pnl = float(t.get('realized_pnl', 0))
        if loss_streak >= streak_threshold and t.get('ai_score') is not None:
            after_streak_scores.append(t['ai_score'])
        loss_streak = loss_streak + 1 if pnl < 0 else 0

    if len(after_streak_scores) < MIN_PATTERN_EVENTS:
        return None
    after_avg = sum(after_streak_scores) / len(after_streak_scores)
    return {
        'baseline_avg_score': round(baseline_avg, 1),
        'after_loss_streak_avg_score': round(after_avg, 1),
        'sample_size': len(after_streak_scores),
        'degrades': after_avg < baseline_avg - 5,
    }


def sl_tp_discipline(trades: list) -> Optional[dict]:
    """Доля сделок, где выставлены и SL, и TP."""
    if not trades:
        return None
    both = sum(1 for t in trades if t.get('stop_loss') and t.get('take_profit'))
    return {
        'total': len(trades),
        'both_set': both,
        'rate': round(both / len(trades) * 100, 1),
    }


def behavior_symbol_pattern(db, symbol: str, event_type: str, user_id: str = 'default') -> Optional[dict]:
    """Приближённый джойн: конкретный поведенческий флаг на конкретном
    символе → как часто после него сделка закрывалась в минус. При >1
    совпадении по времени событие не засчитывается вместо угадывания
    (см. докстринг модуля, риски в TRADER_DNA_V1.md §Roadmap DNA v1)."""
    events = db.get_recent_behavior_events(user_id, event_type=event_type, limit=200)
    if not events:
        return None

    trades = db.get_closed_trades(limit=200, symbol=symbol, user_id=user_id)
    if not trades:
        return None

    window = timedelta(minutes=BEHAVIOR_JOIN_WINDOW_MINUTES)
    # revenge_trading/fomo/overtrading пишутся на открытии позиции, поэтому
    # сравниваем с open_time; panic_close пишется на закрытии — с close_time.
    anchor_field = 'close_time' if event_type == 'panic_close' else 'open_time'

    matched = []
    for ev in events:
        meta_raw = ev.get('metadata')
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except Exception:
            meta = {}
        if meta.get('symbol') != symbol:
            continue
        ev_time = _parse_dt(ev.get('created_at'))
        if not ev_time:
            continue

        candidates = [
            t for t in trades
            if _parse_dt(t.get(anchor_field)) and abs(_parse_dt(t.get(anchor_field)) - ev_time) <= window
        ]
        if len(candidates) == 1:
            matched.append(candidates[0])

    if len(matched) < MIN_PATTERN_EVENTS:
        return None

    negative = sum(1 for t in matched if float(t.get('realized_pnl', 0)) < 0)
    return {
        'symbol': symbol,
        'event_type': event_type,
        'occurrences': len(matched),
        'negative_outcomes': negative,
        'negative_rate': round(negative / len(matched) * 100, 1),
    }


_EVENT_TYPE_LABELS = {
    'fomo': 'входов после импульсного роста',
    'revenge_trading': 'входов сразу после серии убытков',
    'panic_close': 'случаев паникующего закрытия',
}


def build_pattern_insights(db, symbol: str, behavior_type: str = 'fomo', user_id: str = 'default') -> Optional[str]:
    """Персональная рекомендация (TRADER_DNA_V1.md, Фаза 2, «Персональные
    рекомендации»): соединяет исторический паттерн по конкретному
    поведенческому флагу и символу в готовую фразу. Шаблон поверх
    детерминированной статистики, не LLM-генерация. None, если данных
    недостаточно или паттерн не выглядит устойчиво отрицательным."""
    pattern = behavior_symbol_pattern(db, symbol, behavior_type, user_id=user_id)
    if not pattern or pattern['negative_rate'] < 50:
        return None

    label = _EVENT_TYPE_LABELS.get(behavior_type, f'событий «{behavior_type}»')
    return (
        f"ваши последние {pattern['occurrences']} {label} по {symbol} "
        f"имеют отрицательный результат в {pattern['negative_rate']:.0f}% случаев"
    )


def analyze_patterns(db, user_id: str = 'default') -> dict:
    """Фаза 2 целиком: все паттерны из каталога, доступные без новых
    таблиц/агентов (TRADER_DNA_V1.md, раздел «Ошибки трейдера» / «Сильные
    стороны» / «Поведенческие закономерности», строки с ✅)."""
    trades = db.get_closed_trades(limit=200, user_id=user_id)
    if len(trades) < MIN_TRADES_FOR_PATTERNS:
        return {'active': False, 'total_trades': len(trades)}

    return {
        'active': True,
        'total_trades': len(trades),
        'market_regime': market_regime_pattern(trades),
        'holding_time': holding_time_pattern(trades),
        'early_exit': early_exit_pattern(trades),
        'stop_width': stop_width_pattern(trades),
        'quality_after_loss_streak': quality_after_loss_streak_pattern(trades),
        'sl_tp_discipline': sl_tp_discipline(trades),
    }


# ═══════════════════════ Фаза 3: DNA Score ═══════════════════════════════

def _edge_component(basic: dict) -> float:
    """Winrate и profit factor вокруг точек безубыточности (50%/1.0),
    симметрично взвешены, зажаты в 0-100."""
    winrate = basic.get('winrate', 0)
    profit_factor = min(basic.get('profit_factor', 0), 3)
    score = 50 + (winrate - 50) * 0.6 + (profit_factor - 1) * 16.67
    return max(0.0, min(100.0, score))


def _discipline_component(discipline: Optional[dict]) -> Optional[float]:
    if not discipline or discipline['total'] < MIN_TRADES_FOR_PATTERNS:
        return None
    return float(discipline['rate'])


def _behavior_stability_component(db, total_trades: int, user_id: str) -> float:
    events = db.get_recent_behavior_events(user_id, limit=200)
    if not events:
        return 100.0
    rate_per_10 = len(events) / max(total_trades, 1) * 10
    return max(0.0, 100.0 - min(100.0, rate_per_10 * 15))


def _consistency_component(setup: dict, session: dict) -> Optional[float]:
    """Насколько равномерен winrate между бакетами (символ/сессия) с
    достаточным объёмом — не звезда на одном инструменте, провал на
    остальных. Исключается (не штрафуется), если бакетов меньше двух."""
    buckets = [d['winrate'] for d in setup.values() if d['total'] >= MIN_BUCKET_TRADES]
    buckets += [d['winrate'] for d in session.values() if d['total'] >= MIN_BUCKET_TRADES]
    if len(buckets) < 2:
        return None
    spread = max(buckets) - min(buckets)
    return max(0.0, 100.0 - spread)


def compute_dna_score(db, user_id: str = 'default') -> dict:
    """Фаза 3: DNA Score. score=None с explicit статусом, если данных
    недостаточно (DNA_SCORE_MIN_TRADES) — тот же принцип честности, что и
    active=False в ai/trader_context.py:compute_dna_adjustment(). Веса
    зафиксированы константами (DNA_SCORE_WEIGHTS), не подбираются моделью —
    тот же принцип, что и JudgeAgent.WEIGHTS (детерминированный арбитр)."""
    engine = PerformanceEngine(db)
    report = engine.get_full_report(user_id)
    if report.get('empty'):
        return {'score': None, 'confidence': 'insufficient_data', 'total_trades': 0, 'components': {}}

    basic = report.get('basic', {})
    total = basic.get('total', 0)
    if total < DNA_SCORE_MIN_TRADES:
        return {'score': None, 'confidence': 'insufficient_data', 'total_trades': total, 'components': {}}

    discipline_data = sl_tp_discipline(db.get_closed_trades(limit=200, user_id=user_id))

    components = {
        'edge': _edge_component(basic),
        'discipline': _discipline_component(discipline_data),
        'behavior_stability': _behavior_stability_component(db, total, user_id),
        'consistency': _consistency_component(report.get('setup', {}), report.get('session', {})),
    }

    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return {'score': None, 'confidence': 'insufficient_data', 'total_trades': total, 'components': components}

    weight_sum = sum(DNA_SCORE_WEIGHTS[k] for k in available)
    score = sum(v * DNA_SCORE_WEIGHTS[k] for k, v in available.items()) / weight_sum
    confidence = 'low' if total < DNA_SCORE_FULL_CONFIDENCE_TRADES else 'full'

    return {
        'score': round(score),
        'confidence': confidence,
        'total_trades': total,
        'components': {k: (round(v, 1) if v is not None else None) for k, v in components.items()},
    }


# ═══════════════════════ Отчёт для пользователя ═══════════════════════════

_CONFIDENCE_LABELS = {'low': 'низкая уверенность', 'full': 'полная уверенность'}


def format_dna_report(db, user_id: str = 'default') -> str:
    """Форматирует Фазу 2 + Фазу 3 в читаемый текст для Telegram (тот же
    стиль, что format_performance_report)."""
    patterns = analyze_patterns(db, user_id)
    if not patterns.get('active'):
        return (
            "🧬 Trader DNA\n\n"
            f"Недостаточно данных — нужно минимум {MIN_TRADES_FOR_PATTERNS} закрытых сделок, "
            f"сейчас {patterns.get('total_trades', 0)}."
        )

    score_result = compute_dna_score(db, user_id)
    lines = ["🧬 Trader DNA v1\n"]

    if score_result['score'] is not None:
        conf_label = _CONFIDENCE_LABELS[score_result['confidence']]
        lines.append(f"DNA Score: {score_result['score']}/100 ({conf_label}, {score_result['total_trades']} сделок)")
    else:
        lines.append(
            f"DNA Score: недостаточно данных (нужно {DNA_SCORE_MIN_TRADES}+ сделок, "
            f"сейчас {patterns['total_trades']})"
        )
    lines.append("")

    holding = patterns.get('holding_time')
    if holding and holding['overextends_losses']:
        lines.append(
            f"⚠️ Переудержание убытков: в среднем убыточные позиции держите "
            f"{holding['avg_holding_loss']:.0f} мин против {holding['avg_holding_win']:.0f} мин в прибыльных"
        )

    early = patterns.get('early_exit')
    if early and early['early_exit_rate'] >= 40:
        lines.append(
            f"⚠️ Ранняя фиксация прибыли: {early['early_exit_rate']:.0f}% прибыльных сделок "
            f"закрыты меньше чем на половине пути до take profit"
        )

    quality = patterns.get('quality_after_loss_streak')
    if quality and quality['degrades']:
        lines.append(
            f"⚠️ Качество решений падает после серии убытков: средняя оценка "
            f"{quality['after_loss_streak_avg_score']:.0f} против обычной {quality['baseline_avg_score']:.0f}"
        )

    stop_width = patterns.get('stop_width')
    if stop_width and (stop_width['narrow_stop_count'] or stop_width['wide_stop_count']):
        lines.append(
            f"📏 Дистанция стопа: своя норма ~{stop_width['avg_stop_distance_pct']:.1f}% от входа, "
            f"из них узких — {stop_width['narrow_stop_count']}, широких — {stop_width['wide_stop_count']}"
        )

    discipline = patterns.get('sl_tp_discipline')
    if discipline:
        lines.append(f"📐 SL и TP выставлены вместе в {discipline['rate']:.0f}% сделок")

    regime = patterns.get('market_regime')
    if regime:
        best = max(regime.items(), key=lambda kv: kv[1]['winrate'])
        lines.append(f"📊 Лучший рыночный режим: {best[0]} ({best[1]['winrate']:.0f}% winrate)")

    if len(lines) == 2:
        lines.append("Явных отклонений по каталогу паттернов пока не найдено.")

    return "\n".join(lines)
