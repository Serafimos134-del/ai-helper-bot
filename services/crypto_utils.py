"""
services/crypto_utils.py
Шифрование чувствительных полей (API-ключи BingX) перед сохранением в БД.

Ключ шифрования берётся из переменной окружения DB_ENCRYPTION_KEY.
Сгенерировать новый ключ:  python -c "from services.crypto_utils import generate_key; print(generate_key())"
Полученную строку положить в .env как DB_ENCRYPTION_KEY=...

Если переменная не задана — значения сохраняются как есть (plaintext) и в лог
пишется предупреждение. Это сделано, чтобы не ломать локальную разработку,
но для продакшена/продажи бота ключ обязателен.
"""

import os
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_ENV_VAR = 'DB_ENCRYPTION_KEY'
_warned = False


def generate_key() -> str:
    """Сгенерировать новый ключ для DB_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode()


def _get_fernet():
    global _warned
    key = os.environ.get(_ENV_VAR)
    if not key:
        if not _warned:
            logger.warning(
                f"{_ENV_VAR} не задан — API-ключи BingX будут храниться в БД "
                f"НЕЗАШИФРОВАННЫМИ. Сгенерируйте ключ (crypto_utils.generate_key()) "
                f"и добавьте его в .env перед продакшеном."
            )
            _warned = True
        return None
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        logger.error(f"{_ENV_VAR} невалиден (должен быть Fernet-ключом) — работаю без шифрования")
        return None


def encrypt(value: str):
    """Зашифровать строку. None/'' проходят насквозь."""
    if not value:
        return value
    fernet = _get_fernet()
    if fernet is None:
        return value
    return fernet.encrypt(value.encode()).decode()


def decrypt(value: str):
    """Расшифровать строку. Если значение не похоже на токен шифрования
    (например, было сохранено ещё до включения шифрования) — возвращает как есть."""
    if not value:
        return value
    fernet = _get_fernet()
    if fernet is None:
        return value
    try:
        return fernet.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning("decrypt(): значение не похоже на зашифрованный токен — считаю legacy plaintext")
        return value
