from services.database import Database

db = Database()

def save_comment(trade_id: int, text: str) -> bool:
    """Сохранить комментарий (вывод по сделке) в базу данных."""
    try:
        db.add_comment(trade_id, text)
        return True
    except Exception:
        return False