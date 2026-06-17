def format_stats_message(stats: dict) -> str:
    if stats.get('total_trades', 0) == 0:
        return "📊 *Статистика*\n\nНет закрытых сделок для расчёта."

    lines = [
        "📊 *Статистика торговли*\n",
        f"📈 Всего сделок: {stats['total_trades']}",
        f"✅ Прибыльных: {stats['winning_trades']}",
        f"❌ Убыточных: {stats['losing_trades']}",
        f"🎯 Win Rate: {stats['win_rate']:.1f}%\n",
        f"🟢 Общий PNL: ${stats['total_pnl']:.2f}",
        f"📈 Средняя прибыль: ${stats['avg_profit']:.2f}",
        f"📉 Средний убыток: ${stats['avg_loss']:.2f}",
        f"🟡 Нереализованный PNL: ${stats['unrealized_pnl']:.2f}",
        f"🔓 Открытых позиций: {stats['open_positions']}\n",
    ]
    if stats.get('best_trade_symbol'):
        lines.append(f"🏆 Лучшая: {stats['best_trade_symbol']} +${stats['best_trade']:.2f}")
    else:
        lines.append(f"🏆 Лучшая: ${stats['best_trade']:.2f}")
    if stats.get('worst_trade_symbol'):
        lines.append(f"💀 Худшая: {stats['worst_trade_symbol']} ${stats['worst_trade']:.2f}")
    else:
        lines.append(f"💀 Худшая: ${stats['worst_trade']:.2f}")
    return "\n".join(lines)