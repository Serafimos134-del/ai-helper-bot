from services.trading_storage import get_closed_trades, get_open_trades


def calculate_stats() -> dict:
    """Рассчитать полную статистику торговли."""
    closed = get_closed_trades()
    open_trades = get_open_trades()

    if not closed:
        return {
            'total_trades': 0,
            'open_trades': len(open_trades),
            'win_rate': 0.0,
            'total_pnl': 0.0,
            'avg_profit': 0.0,
            'avg_loss': 0.0,
            'best_trade': None,
            'worst_trade': None,
            'wins': 0,
            'losses': 0,
            'unrealized_pnl': sum(float(t.get('unrealizedPnl', 0)) for t in open_trades)
        }

    pnl_values = []
    for trade in closed:
        pnl = float(trade.get('realizedPnl', trade.get('pnl', 0)))
        pnl_values.append((pnl, trade))

    wins = [(pnl, t) for pnl, t in pnl_values if pnl > 0]
    losses = [(pnl, t) for pnl, t in pnl_values if pnl <= 0]

    total_pnl = sum(pnl for pnl, _ in pnl_values)
    win_rate = (len(wins) / len(pnl_values) * 100) if pnl_values else 0.0
    avg_profit = (sum(pnl for pnl, _ in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(pnl for pnl, _ in losses) / len(losses)) if losses else 0.0

    best_trade = max(pnl_values, key=lambda x: x[0])[1] if pnl_values else None
    worst_trade = min(pnl_values, key=lambda x: x[0])[1] if pnl_values else None

    unrealized_pnl = sum(float(t.get('unrealizedPnl', 0)) for t in open_trades)

    return {
        'total_trades': len(closed),
        'open_trades': len(open_trades),
        'win_rate': round(win_rate, 1),
        'total_pnl': round(total_pnl, 2),
        'avg_profit': round(avg_profit, 2),
        'avg_loss': round(avg_loss, 2),
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'wins': len(wins),
        'losses': len(losses),
        'unrealized_pnl': round(unrealized_pnl, 2)
    }


def format_stats_message(stats: dict) -> str:
    """Форматировать статистику в читаемое сообщение."""
    if stats['total_trades'] == 0:
        return (
            "📊 *Статистика торговли*\n\n"
            "Закрытых сделок пока нет.\n"
            f"Открытых позиций: {stats['open_trades']}"
        )

    best = stats['best_trade']
    worst = stats['worst_trade']

    best_str = ""
    if best:
        best_pnl = float(best.get('realizedPnl', best.get('pnl', 0)))
        best_str = f"\n🏆 Лучшая: {best.get('symbol', '?')} +${best_pnl:.2f}"

    worst_str = ""
    if worst:
        worst_pnl = float(worst.get('realizedPnl', worst.get('pnl', 0)))
        worst_str = f"\n💀 Худшая: {worst.get('symbol', '?')} ${worst_pnl:.2f}"

    pnl_emoji = "🟢" if stats['total_pnl'] >= 0 else "🔴"
    unrealized_emoji = "🟡" if stats['unrealized_pnl'] >= 0 else "🔴"

    return (
        "📊 *Статистика торговли*\n\n"
        f"📈 Всего сделок: {stats['total_trades']}\n"
        f"✅ Прибыльных: {stats['wins']}\n"
        f"❌ Убыточных: {stats['losses']}\n"
        f"🎯 Win Rate: {stats['win_rate']}%\n\n"
        f"{pnl_emoji} Общий PNL: ${stats['total_pnl']:.2f}\n"
        f"📈 Средняя прибыль: ${stats['avg_profit']:.2f}\n"
        f"📉 Средний убыток: ${stats['avg_loss']:.2f}\n"
        f"{unrealized_emoji} Нереализованный PNL: ${stats['unrealized_pnl']:.2f}\n"
        f"🔓 Открытых позиций: {stats['open_trades']}"
        f"{best_str}{worst_str}"
    )
