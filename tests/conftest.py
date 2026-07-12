"""
tests/conftest.py
Общие фикстуры для всего тестового набора (Unit/Integration/E2E — задача
"Тестирование" от 12.07.2026).

Переменные окружения выставляются здесь, до импорта любого модуля
проекта (многие читают os.getenv на уровне модуля — например,
core/user_context.py:_OWNER_CHAT_ID) — pytest импортирует conftest.py
раньше тестовых файлов, это гарантированная точка для этого.

Database — синглтон (services/database.py), поэтому фикстура db
явно сбрасывает Database._instance и создаёт новый экземпляр на
отдельном временном файле для каждого теста — без этого тесты делили бы
один и тот же файл БД и мешали друг другу.
"""

import os
import sys

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'test_bot_token')
os.environ.setdefault('TELEGRAM_CHAT_ID', '111')
os.environ.setdefault('BINGX_API_KEY', 'GLOBAL_TEST_KEY')
os.environ.setdefault('BINGX_SECRET_KEY', 'GLOBAL_TEST_SECRET')
os.environ.setdefault('GROQ_API_KEY', 'test_groq_key')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

OWNER_TELEGRAM_ID = os.environ['TELEGRAM_CHAT_ID']


@pytest.fixture
def db(tmp_path):
    from services.database import Database
    Database._instance = None
    instance = Database(db_path=str(tmp_path / "test.db"))
    yield instance
    Database._instance = None


@pytest.fixture
def make_user(db):
    """Создаёт пользователя с telegram_id (по умолчанию не владелец)."""
    def _make(telegram_id: str, username: str = "testuser"):
        return db.get_or_create_user(telegram_id, username)
    return _make


@pytest.fixture
def owner_user(db):
    return db.get_or_create_user(OWNER_TELEGRAM_ID, "owner")
