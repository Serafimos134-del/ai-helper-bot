"""
core/user_context.py
Мультитенантность (см. MULTITENANCY_MIGRATION_PLAN.md, Этап 1) —
middleware-хендлер, резолвящий текущего Telegram-пользователя, его биржу
и её ключи один раз на апдейт, до всех остальных хендлеров.

Регистрируется в group=-1 (core/scheduler.py/bot.py) — в python-telegram-bot
группы с меньшим номером обрабатываются раньше; TypeHandler(Update, ...)
матчит любой тип апдейта (команда, текст, callback_query), поэтому один
хендлер покрывает все точки входа, не требуя добавлять резолвинг в каждый
обработчик отдельно.

Адаптер+ключи прокидываются в services/exchange_api.py через contextvars,
а не параметром через ContextBuilder/AIOrchestrator/ConsensusEngine — см.
докстринг там же (Exchange Adapter Layer, задача от 12.07.2026).
"""

import os
import logging
from telegram import Update
from telegram.ext import ContextTypes

from core.container import get_db
from services.exchange_api import set_current_exchange, clear_current_exchange

logger = logging.getLogger(__name__)

# Владелец бота — временный admin-доступ на переходный период миграции
# (Crypto Pay ещё не подключён, Этап 4). Владелец всегда авторизован,
# независимо от подписки — не должен сам себя заблокировать, пока оплата
# не готова. После полного перехода на подписки это можно убрать или
# оставить как aдминский bypass для поддержки.
_OWNER_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')


async def resolve_user_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ВАЖНО: сбрасываем адаптер/ключи ПЕРВЫМ делом, до любых DB-запросов/
    # ранних return. PTB по умолчанию (concurrent_updates выключен, как
    # сейчас в bot.py) обрабатывает апдейты последовательно В ОДНОМ И ТОМ
    # ЖЕ таске — если не сбросить явно, contextvar сохраняет значение,
    # установленное ПРЕДЫДУЩИМ апдейтом (чужим пользователем), и любой
    # ранний return ниже (нет update.effective_user, ошибка БД, нет своих
    # ключей) тихо оставил бы чужие ключи активными для этого запроса.
    # Поймано смоук-тестом при разработке — без явного clear() здесь
    # пользователь без своих ключей получал ключи ПРЕДЫДУЩЕГО
    # обработанного пользователя вместо глобального fallback.
    clear_current_exchange()

    user = update.effective_user
    if not user:
        context.user_data['is_authorized'] = False
        return

    db = get_db()
    telegram_id = str(user.id)
    try:
        db_user = db.get_or_create_user(telegram_id, user.username)
    except Exception as e:
        logger.error(f"resolve_user_context: не удалось получить/создать пользователя {telegram_id}: {e}")
        context.user_data['is_authorized'] = False
        return

    context.user_data['user'] = db_user

    is_owner = bool(_OWNER_CHAT_ID) and telegram_id == _OWNER_CHAT_ID
    try:
        is_subscribed = db.is_premium(db_user['user_id'])
    except Exception as e:
        logger.error(f"resolve_user_context: не удалось проверить подписку {db_user['user_id']}: {e}")
        is_subscribed = False
    context.user_data['is_owner'] = is_owner
    context.user_data['is_authorized'] = bool(is_owner or is_subscribed)

    exchange = db_user.get('exchange') or 'bingx'
    try:
        # Хранение ключей остаётся в bingx_api_key/bingx_secret_key (см.
        # services/database.py) — единственная реализованная биржа; при
        # добавлении второй потребуется своя схема хранения ключей.
        api_key, secret_key = db.get_bingx_keys(db_user['user_id'])
    except Exception as e:
        logger.error(f"resolve_user_context: не удалось получить ключи биржи для {db_user['user_id']}: {e}")
        api_key, secret_key = None, None

    set_current_exchange(exchange, api_key, secret_key)
    # Если своих ключей нет — адаптер очищен внутри set_current_exchange:
    # BingXAdapter откатится на глобальный .env fallback (текущий
    # single-user режим, пока онбординг ключей не пройден всеми
    # пользователями), а не на ключи чужого предыдущего запроса.


def get_current_user_id(context: ContextTypes.DEFAULT_TYPE, default: str = 'default') -> str:
    """Удобный хелпер для хендлеров — user_id текущего пользователя (уже
    зарезолвлен resolve_user_context на этот апдейт), с фолбэком на
    'default' для путей, куда мидлварь ещё не докатилась (фоновые джобы)."""
    user = context.user_data.get('user') if context and context.user_data else None
    return user['user_id'] if user else default


async def require_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True, если пользователь авторизован (владелец или активная
    подписка — см. resolve_user_context). Если нет — отвечает
    пользователю и возвращает False. Заменяет старую проверку
    `str(update.effective_chat.id) != CHAT_ID` во всех хендлерах —
    единая точка того, "кто имеет право пользоваться ботом" (см.
    MULTITENANCY_MIGRATION_PLAN.md, Этап 3)."""
    if context.user_data.get('is_authorized'):
        return True
    text = "🔒 Нужна активная подписка. Наберите /start, чтобы узнать подробности."
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.message:
        await update.message.reply_text(text)
    return False
