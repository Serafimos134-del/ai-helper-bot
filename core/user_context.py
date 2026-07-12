"""
core/user_context.py
Мультитенантность (см. MULTITENANCY_MIGRATION_PLAN.md, Этап 1) —
middleware-хендлер, резолвящий текущего Telegram-пользователя и его
BingX-ключи один раз на апдейт, до всех остальных хендлеров.

Регистрируется в group=-1 (core/scheduler.py/bot.py) — в python-telegram-bot
группы с меньшим номером обрабатываются раньше; TypeHandler(Update, ...)
матчит любой тип апдейта (команда, текст, callback_query), поэтому один
хендлер покрывает все точки входа, не требуя добавлять резолвинг в каждый
обработчик отдельно.

Ключи прокидываются в services/bingx_api.py через contextvars, а не
параметром через ContextBuilder/AIOrchestrator/ConsensusEngine — см.
докстринг там же.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from core.container import get_db
from services.bingx_api import set_bingx_credentials, clear_bingx_credentials

logger = logging.getLogger(__name__)


async def resolve_user_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ВАЖНО: сбрасываем ключи ПЕРВЫМ делом, до любых DB-запросов/ранних
    # return. PTB по умолчанию (concurrent_updates выключен, как сейчас в
    # bot.py) обрабатывает апдейты последовательно В ОДНОМ И ТОМ ЖЕ таске —
    # если не сбросить явно, contextvar сохраняет значение, установленное
    # ПРЕДЫДУЩИМ апдейтом (чужим пользователем), и любой ранний return
    # ниже (нет update.effective_user, ошибка БД, нет своих ключей) тихо
    # оставил бы чужие ключи активными для этого запроса. Поймано
    # смоук-тестом при разработке — без явного clear() здесь пользователь
    # без своих ключей получал BingX-ключи ПРЕДЫДУЩЕГО обработанного
    # пользователя вместо глобального fallback.
    clear_bingx_credentials()

    user = update.effective_user
    if not user:
        return

    db = get_db()
    telegram_id = str(user.id)
    try:
        db_user = db.get_or_create_user(telegram_id, user.username)
    except Exception as e:
        logger.error(f"resolve_user_context: не удалось получить/создать пользователя {telegram_id}: {e}")
        return

    context.user_data['user'] = db_user

    try:
        api_key, secret_key = db.get_bingx_keys(db_user['user_id'])
    except Exception as e:
        logger.error(f"resolve_user_context: не удалось получить BingX-ключи для {db_user['user_id']}: {e}")
        api_key, secret_key = None, None

    if api_key and secret_key:
        set_bingx_credentials(api_key, secret_key)
    # Если своих ключей нет — оставляем сброшенным (сделано в начале
    # функции): bingx_api._get_credentials() откатится на глобальный .env
    # fallback (текущий single-user режим, пока онбординг ключей не
    # пройден всеми пользователями), а не на ключи чужого предыдущего
    # запроса.


def get_current_user_id(context: ContextTypes.DEFAULT_TYPE, default: str = 'default') -> str:
    """Удобный хелпер для хендлеров — user_id текущего пользователя (уже
    зарезолвлен resolve_user_context на этот апдейт), с фолбэком на
    'default' для путей, куда мидлварь ещё не докатилась (фоновые джобы)."""
    user = context.user_data.get('user') if context and context.user_data else None
    return user['user_id'] if user else default
