from services.database import Database

db = Database()

def save_comment(trade_id: int, text: str, user_id: str = None) -> bool:
    """Сохранить комментарий (вывод по сделке) в базу данных. user_id —
    изоляция от чужих сделок (см. MULTITENANCY_MIGRATION_PLAN.md)."""
    try:
        db.add_comment(trade_id, text, user_id)
        return True
    except Exception:
        return False