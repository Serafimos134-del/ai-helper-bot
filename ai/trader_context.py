"""
ai/trader_context.py
TraderContext (см. TRADER_INTELLIGENCE_ARCHITECTURE.md, §6) — единый
runtime-объект персонального контекста трейдера. НЕ новая таблица:
собирается заново на каждый вызов из уже существующих источников
(PerformanceEngine, BehaviorEngine, trader_memory), ничего не персистирует.

Также здесь — compute_dna_adjustment(): advisory-only поправка для
JudgeAgent. Первая версия интеграции (см. §7 архитектурного документа)
намеренно ограничена по влиянию:
  - активируется только при достаточной выборке (MIN_TOTAL_TRADES);
  - поправка по символу требует отдельного минимума (MIN_SYMBOL_TRADES);
  - итоговая поправка не может превышать MAX_SCORE_ADJUSTMENT по модулю —
    TraderContext может подтолкнуть решение, но не развернуть его.
"""

import logging
from typing import Optional

from services.performance_engine import PerformanceEngine

logger = logging.getLogger(__name__)

# Категории trader_memory, которые действительно относятся к трейдерской
# статистике (MemoryEngine) — не служебное состояние бота (position_watch,
# bot_state, missing_cycles), см. TRADER_INTELLIGENCE_ARCHITECTURE.md, §3.
_MEMORY_CATEGORIES = ("global", "ticker", "direction", "holding")

RECENT_BEHAVIOR_LIMIT = 10

# Пороги advisory-режима (см. докстринг модуля и §9 архитектурного документа).
MIN_TOTAL_TRADES = 5
MIN_SYMBOL_TRADES = 3
MAX_SCORE_ADJUSTMENT = 10

# Насколько winrate по символу должен отличаться от общего winrate, чтобы
# засчитать это как значимое отклонение (не шум на малой выборке).
SYMBOL_WINRATE_DELTA_NEGATIVE = -20
SYMBOL_WINRATE_DELTA_POSITIVE = 20


def build_trader_context(db, symbol: Optional[str] = None, user_id: str = "default") -> dict:
    """Собирает TraderContext для текущего запроса. Ничего не пишет в БД."""
    engine = PerformanceEngine(db)
    report = engine.get_full_report(user_id)

    if report.get("empty"):
        return {
            "profile": {"total_trades": 0, "winrate": 0, "profit_factor": 0, "avg_rr": 0, "avg_leverage": 0},
            "edges": {"best_symbol": None, "worst_symbol": None, "best_session": None, "worst_session": None},
            "streaks": {"current_loss_streak": 0, "current_win_streak": 0, "max_loss_streak": 0},
            "behavior": {"recent_flags": [], "flag_counts": {}},
            "symbol_context": None,
        }

    basic = report.get("basic", {})
    rr = report.get("rr", {})
    lev = report.get("leverage", {})
    streak = report.get("streak", {})
    setup = report.get("setup", {})
    session = report.get("session", {})

    profile = {
        "total_trades": basic.get("total", 0),
        "winrate": basic.get("winrate", 0),
        "profit_factor": basic.get("profit_factor", 0),
        "avg_rr": rr.get("rr_ratio", 0),
        "avg_leverage": lev.get("avg_leverage", 0),
    }

    symbols_by_pnl = list(setup.items())  # уже отсортированы по pnl убыв. в PerformanceEngine
    edges = {
        "best_symbol": symbols_by_pnl[0][0] if symbols_by_pnl else None,
        "worst_symbol": symbols_by_pnl[-1][0] if symbols_by_pnl else None,
        "best_session": _extreme_session(session, best=True),
        "worst_session": _extreme_session(session, best=False),
    }

    streaks = {
        "current_loss_streak": streak.get("current_loss_streak", 0),
        "current_win_streak": streak.get("current_win_streak", 0),
        "max_loss_streak": streak.get("max_loss_streak", 0),
    }

    behavior = _build_behavior_summary(db, user_id)

    symbol_context = None
    if symbol and symbol in setup:
        d = setup[symbol]
        symbol_context = {"symbol": symbol, "trades": d["total"], "winrate": d["winrate"], "pnl": d["pnl"]}

    return {
        "profile": profile,
        "edges": edges,
        "streaks": streaks,
        "behavior": behavior,
        "symbol_context": symbol_context,
    }


def _extreme_session(session: dict, best: bool) -> Optional[str]:
    eligible = {k: v for k, v in session.items() if v.get("total", 0) >= 2}
    if not eligible:
        return None
    key = max(eligible, key=lambda k: eligible[k]["winrate"]) if best else \
          min(eligible, key=lambda k: eligible[k]["winrate"])
    return eligible[key]["label"]


def _build_behavior_summary(db, user_id: str) -> dict:
    try:
        events = db.get_recent_behavior_events(user_id, limit=RECENT_BEHAVIOR_LIMIT)
    except Exception as e:
        logger.error(f"TraderContext: не удалось получить behavior_events: {e}")
        return {"recent_flags": [], "flag_counts": {}}

    flag_counts = {}
    recent_flags = []
    for ev in events:
        event_type = ev.get("event_type")
        flag_counts[event_type] = flag_counts.get(event_type, 0) + 1
        recent_flags.append({
            "event_type": event_type,
            "severity": ev.get("severity"),
            "created_at": str(ev.get("created_at", "")),
        })

    return {"recent_flags": recent_flags, "flag_counts": flag_counts}


def compute_dna_adjustment(trader_context: Optional[dict]) -> dict:
    """Advisory-only поправка к final_score. Всегда возвращает структуру
    {score_delta, reason, active} — active=False означает "не хватает
    данных, поправка не применяется" (а не "поправка равна нулю по сути
    оценки"), чтобы вызывающий код мог явно это показать в демонстрации."""
    if not trader_context:
        return {"score_delta": 0, "reason": None, "active": False}

    profile = trader_context.get("profile", {})
    if profile.get("total_trades", 0) < MIN_TOTAL_TRADES:
        return {"score_delta": 0, "reason": None, "active": False}

    delta = 0
    reasons = []

    symbol_ctx = trader_context.get("symbol_context")
    if symbol_ctx and symbol_ctx.get("trades", 0) >= MIN_SYMBOL_TRADES:
        wr_diff = symbol_ctx["winrate"] - profile.get("winrate", 0)
        if wr_diff <= SYMBOL_WINRATE_DELTA_NEGATIVE:
            delta -= 8
            reasons.append(
                f"по {symbol_ctx['symbol']} winrate {symbol_ctx['winrate']:.0f}% "
                f"против среднего {profile.get('winrate', 0):.0f}%"
            )
        elif wr_diff >= SYMBOL_WINRATE_DELTA_POSITIVE:
            delta += 4
            reasons.append(
                f"по {symbol_ctx['symbol']} winrate {symbol_ctx['winrate']:.0f}% "
                f"выше среднего {profile.get('winrate', 0):.0f}%"
            )

    streaks = trader_context.get("streaks", {})
    loss_streak = streaks.get("current_loss_streak", 0)
    if loss_streak >= 3:
        delta -= 6
        reasons.append(f"серия из {loss_streak} убытков подряд")
    elif loss_streak >= 2:
        delta -= 3
        reasons.append(f"{loss_streak} убытка подряд")

    flag_counts = trader_context.get("behavior", {}).get("flag_counts", {})
    if flag_counts.get("revenge_trading", 0) > 0:
        delta -= 5
        reasons.append("недавние признаки revenge trading")
    if flag_counts.get("fomo", 0) > 0:
        delta -= 3
        reasons.append("недавние признаки FOMO")

    clamped = max(-MAX_SCORE_ADJUSTMENT, min(MAX_SCORE_ADJUSTMENT, delta))

    return {
        "score_delta": clamped,
        "reason": "; ".join(reasons) if reasons else None,
        "active": True,
    }


def format_trader_context_summary(trader_context: Optional[dict]) -> str:
    """Короткая текстовая сводка TraderContext для футера ответа пользователю.
    Заменяет старый ContextBuilder._get_memory_context_sync() (основанный на
    отдельных счётчиках MemoryEngine, см. TRADER_INTELLIGENCE_ARCHITECTURE.md,
    §1.3/§8, Этап 5) — теперь источник один: тот же TraderContext, что видит
    JudgeAgent, а не отдельный пересчёт из trader_memory."""
    if not trader_context:
        return ""
    profile = trader_context.get("profile", {})
    total = profile.get("total_trades", 0)
    if total < 2:
        return ""

    edges = trader_context.get("edges", {})
    lines = ["ПРОФИЛЬ ТРЕЙДЕРА (на основе истории):"]
    lines.append(f"- Всего сделок: {total} (winrate: {profile.get('winrate', 0):.0f}%)")
    if edges.get("best_symbol"):
        lines.append(f"- Лучший символ: {edges['best_symbol']}")
    if edges.get("worst_symbol") and edges.get("worst_symbol") != edges.get("best_symbol"):
        lines.append(f"- Худший символ: {edges['worst_symbol']}")
    return "\n".join(lines) + "\n\n"
