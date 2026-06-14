import json
import os
from datetime import datetime

DATA_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'trading.json')


def _ensure_file():
    """Создать файл данных если не существует."""
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump({"open_trades": [], "closed_trades": []}, f, ensure_ascii=False, indent=2)


def load_trades() -> dict:
    """Загрузить все сделки из файла. Если файл повреждён или содержит
    неправильную структуру (например, список вместо словаря), пересоздаёт его."""
    _ensure_file()
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError):
        data = None

    if not isinstance(data, dict):
        data = {"open_trades": [], "closed_trades": []}
        save_trades(data)
        return data

    if 'open_trades' not in data or not isinstance(data.get('open_trades'), list):
        data['open_trades'] = []
    if 'closed_trades' not in data or not isinstance(data.get('closed_trades'), list):
        data['closed_trades'] = []

    return data


def save_trades(data: dict):
    """Сохранить все сделки в файл."""
    _ensure_file()
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_trade(trade: dict):
    """Добавить новую открытую сделку."""
    data = load_trades()
    # Проверяем, нет ли уже такой сделки по orderId
    existing_ids = {t.get('orderId') for t in data['open_trades']}
    if trade.get('orderId') not in existing_ids:
        trade['saved_at'] = datetime.utcnow().isoformat()
        trade['comment'] = trade.get('comment', '')
        data['open_trades'].append(trade)
        save_trades(data)
        return True
    return False


def close_trade(order_id: str, close_info: dict = None):
    """Перенести сделку из открытых в закрытые."""
    data = load_trades()
    trade_to_close = None

    for trade in data['open_trades']:
        if str(trade.get('orderId')) == str(order_id):
            trade_to_close = trade
            break

    if trade_to_close:
        data['open_trades'].remove(trade_to_close)
        trade_to_close['closed_at'] = datetime.utcnow().isoformat()
        if close_info:
            trade_to_close.update(close_info)
        data['closed_trades'].append(trade_to_close)
        save_trades(data)
        return True
    return False


def get_open_trades() -> list:
    """Получить все открытые сделки."""
    return load_trades().get('open_trades', [])


def get_closed_trades() -> list:
    """Получить все закрытые сделки."""
    return load_trades().get('closed_trades', [])


def add_comment_to_trade(order_id: str, comment: str) -> bool:
    """Добавить комментарий к сделке (открытой или закрытой)."""
    data = load_trades()
    for trade in data['open_trades'] + data['closed_trades']:
        if str(trade.get('orderId')) == str(order_id):
            trade['comment'] = comment
            save_trades(data)
            return True
    return False


def get_all_trades() -> list:
    """Получить все сделки (открытые + закрытые)."""
    data = load_trades()
    return data.get('open_trades', []) + data.get('closed_trades', [])
