"""
handlers/onboarding.py
Мультитенантность, Этап 2 (см. MULTITENANCY_MIGRATION_PLAN.md) — привязка
пользователем своих BingX API-ключей (только чтение) + валидация реальным
запросом к бирже перед сохранением (зашифрованно, services/crypto_utils.py).

/setkeys открыт независимо от подписки: привязка ключей — часть онбординга,
который логически предшествует использованию платных функций (Этап 4,
Crypto Pay, ещё не подключён); сами торговые/AI-хендлеры уже гейтятся
require_auth() (Этап 3), так что открытый /setkeys не даёт доступа ни к
чему платному сам по себе.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from core.container import get_db
from core.keyboards import cancel_keyboard, main_menu_keyboard
from core.user_context import get_current_user_id
from services.bingx_api import validate_keys

logger = logging.getLogger(__name__)

_INSTRUCTIONS = (
    "🔑 Привяжем твои BingX API-ключи.\n\n"
    "⚠️ ВАЖНО: создавай ключ с правами ТОЛЬКО НА ЧТЕНИЕ (Read-Only). "
    "НЕ включай торговлю и вывод средств — боту для аналитики это не нужно, "
    "а тебе так безопаснее.\n\n"
    "Как получить: BingX → Аккаунт → API Management → Create API Key → "
    "оставь только разрешение «Read».\n\n"
    "Пришли API Key (или «отмена»):"
)


async def setkeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'awaiting_bingx_key'
    context.user_data.pop('pending_bingx_api_key', None)
    await update.message.reply_text(_INSTRUCTIONS, reply_markup=cancel_keyboard())


async def handle_awaiting_bingx_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    if len(api_key) < 10:
        await update.message.reply_text(
            "Похоже, это не API Key. Пришли ключ ещё раз (или «отмена»):",
            reply_markup=cancel_keyboard()
        )
        return
    context.user_data['pending_bingx_api_key'] = api_key
    context.user_data['state'] = 'awaiting_bingx_secret'
    await update.message.reply_text(
        "Принято. Теперь пришли Secret Key (сообщение с ним я сразу удалю из чата):",
        reply_markup=cancel_keyboard()
    )


async def handle_awaiting_bingx_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    secret_key = update.message.text.strip()
    api_key = context.user_data.get('pending_bingx_api_key')
    context.user_data['state'] = None
    context.user_data.pop('pending_bingx_api_key', None)

    try:
        await update.message.delete()
    except Exception:
        pass

    if not api_key or len(secret_key) < 10:
        await update.effective_chat.send_message(
            "Что-то пошло не так, начни заново: /setkeys", reply_markup=main_menu_keyboard()
        )
        return

    msg = await update.effective_chat.send_message("⏳ Проверяю ключи на бирже...")
    result = await validate_keys(api_key, secret_key)

    if not result.get('success'):
        await msg.edit_text(
            f"❌ Не удалось подтвердить ключи: {result.get('error', 'неизвестная ошибка')}\n\n"
            f"Проверь, что ключ скопирован полностью и что для него включён доступ к Futures (USDT-M). "
            f"Попробуй ещё раз: /setkeys"
        )
        return

    db = get_db()
    user_id = get_current_user_id(context)
    try:
        db.set_bingx_keys(user_id, api_key, secret_key)
    except Exception as e:
        logger.error(f"setkeys: не удалось сохранить ключи для {user_id}: {e}")
        await msg.edit_text("❌ Ключи проверены, но не удалось их сохранить. Попробуй ещё раз позже: /setkeys")
        return

    await msg.edit_text(
        "✅ Ключи подтверждены и сохранены (в зашифрованном виде).\n"
        f"Баланс аккаунта: ${result['equity']:.2f} USDT.\n\n"
        "Теперь бот работает с твоим аккаунтом."
    )
    await update.effective_chat.send_message("Главное меню:", reply_markup=main_menu_keyboard())
