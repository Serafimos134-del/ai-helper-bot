from services.trading_storage import (
    get_open_trades,
    get_closed_trades,
    add_comment_to_trade
)


def get_trades_for_comment() -> list:
    """Получить список сделок для выбора при добавлении комментария."""
    open_trades = get_open_trades()
    closed_trades = get_closed_trades()[-10:]  # последние 10 закрытых

    result = []
    for t in open_trades:
        result.append({
            'orderId': str(t.get('orderId', '')),
            'label': f"🔓 {t.get('symbol', '?')} {t.get('side', '')} (открыта)",
            'status': 'OPEN'
        })
    for t in reversed(closed_trades):
        pnl = float(t.get('realizedPnl', t.get('pnl', 0)))
        emoji = "✅" if pnl >= 0 else "❌"
        result.append({
            'orderId': str(t.get('orderId', '')),
            'label': f"{emoji} {t.get('symbol', '?')} ${pnl:+.2f}",
            'status': 'CLOSED'
        })
    return result


def save_comment(order_id: str, comment: str) -> bool:
    """Сохранить комментарий к сделке."""
    return add_comment_to_trade(order_id, comment)


def get_comment_template(reason: str) -> str:
    """Получить шаблон комментария по типу причины."""
    templates = {
        'entry': (
            "📥 Причина входа:\n"
            "Почему вошел: \n"
            "Уровень входа: \n"
            "Стоп-лосс: \n"
            "Тейк-профит: "
        ),
        'exit': (
            "📤 Причина выхода:\n"
            "Почему вышел: \n"
            "Результат: \n"
            "Что пошло не так: "
        ),
        'lesson': (
            "📚 Урок из сделки:\n"
            "Ошибка: \n"
            "Что узнал: \n"
            "Что изменю: "
        ),
    }
    return templates.get(reason, '')
