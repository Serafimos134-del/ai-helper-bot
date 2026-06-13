from services.trading_storage import get_closed_trades, get_open_trades
from services.trading_stats import calculate_stats


def get_ai_analysis() -> str:
    """
    Заготовка для AI-анализа торговли.
    В будущем здесь будет реальный AI-модуль.
    Пока возвращает базовый анализ на основе статистики.
    """
    closed = get_closed_trades()
    open_trades = get_open_trades()
    stats = calculate_stats()

    if not closed:
        return (
            "🤖 *AI-анализ*\n\n"
            "Пока нет закрытых сделок для анализа.\n"
            "Торгуй больше — AI найдёт паттерны! 📈"
        )

    observations = []
    suggestions = []

    # Win Rate анализ
    if stats['win_rate'] >= 60:
        observations.append(f"✅ Win Rate {stats['win_rate']}% — хороший результат")
    elif stats['win_rate'] >= 40:
        observations.append(f"⚠️ Win Rate {stats['win_rate']}% — есть куда расти")
    else:
        observations.append(f"❌ Win Rate {stats['win_rate']}% — нужна работа над стратегией")
        suggestions.append("Пересмотри критерии входа в сделку")

    # Risk/Reward анализ
    if stats['avg_profit'] > 0 and stats['avg_loss'] < 0:
        rr = abs(stats['avg_profit'] / stats['avg_loss'])
        if rr >= 2:
            observations.append(f"✅ R/R = {rr:.1f} — отличное соотношение риск/прибыль")
        elif rr >= 1:
            observations.append(f"⚠️ R/R = {rr:.1f} — соотношение приемлемое")
        else:
            observations.append(f"❌ R/R = {rr:.1f} — прибыль меньше убытка")
            suggestions.append("Увеличь тейк-профит или уменьши стоп-лосс")

    # PNL анализ
    if stats['total_pnl'] > 0:
        observations.append(f"✅ Общий PNL положительный: +${stats['total_pnl']:.2f}")
    else:
        observations.append(f"❌ Общий PNL отрицательный: ${stats['total_pnl']:.2f}")
        suggestions.append("Работай над управлением рисками")

    # Анализ комментариев
    trades_with_comments = [t for t in closed if t.get('comment')]
    comment_rate = len(trades_with_comments) / len(closed) * 100 if closed else 0
    if comment_rate < 50:
        suggestions.append("Добавляй больше комментариев к сделкам — это помогает найти паттерны")

    result = "🤖 *AI-анализ торговли*\n\n"
    result += f"📊 Проанализировано сделок: {stats['total_trades']}\n\n"

    if observations:
        result += "🔍 *Наблюдения:*\n"
        result += "\n".join(observations) + "\n\n"

    if suggestions:
        result += "💡 *Рекомендации:*\n"
        result += "\n".join(f"• {s}" for s in suggestions) + "\n\n"

    result += "_В будущем здесь будет полноценный AI с анализом паттернов_ 🚀"
    return result


def prepare_data_for_ai() -> dict:
    """Подготовить данные для будущего AI-модуля."""
    closed = get_closed_trades()
    stats = calculate_stats()

    # Анализ по символам
    symbols = {}
    for trade in closed:
        symbol = trade.get('symbol', 'UNKNOWN')
        if symbol not in symbols:
            symbols[symbol] = {'count': 0, 'total_pnl': 0.0, 'wins': 0}
        pnl = float(trade.get('realizedPnl', trade.get('pnl', 0)))
        symbols[symbol]['count'] += 1
        symbols[symbol]['total_pnl'] += pnl
        if pnl > 0:
            symbols[symbol]['wins'] += 1

    return {
        'stats': stats,
        'symbols': symbols,
        'total_closed': len(closed),
        'trades_with_comments': len([t for t in closed if t.get('comment')])
    }
