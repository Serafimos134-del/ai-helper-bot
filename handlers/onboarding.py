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

import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes

from core.container import get_db
from core.keyboards import cancel_keyboard, main_menu_keyboard
from core.user_context import get_current_user_id, require_auth
from services.bingx_api import validate_keys, set_bingx_credentials, clear_bingx_credentials

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
        "Теперь бот работает с твоим аккаунтом.\n\n"
        "⏳ Подтягиваю историю закрытых сделок с биржи в фоне (до ~15 секунд) — "
        "пришлю итог отдельным сообщением."
    )
    await update.effective_chat.send_message("Главное меню:", reply_markup=main_menu_keyboard())

    # Фоново, чтобы не задерживать ответ пользователю — /riskscore (Этап
    # "персональная модель риска") требует минимум 5 закрытых сделок,
    # ждать их накопления через обычный периодический sync могло бы занять
    # недели. Ключи передаём явно (set_bingx_credentials/clear в finally),
    # а не полагаемся на ambient contextvar — тот к этому моменту уже
    # сброшен обратно middleware'ом (core/user_context.py) для этого же
    # запроса (validate_keys() сама восстанавливает прежнее значение через
    # token/reset после проверки, см. services/bingx_api.py).
    asyncio.create_task(
        _run_background_history_import(user_id, update.effective_chat.id, api_key, secret_key, context.bot)
    )


async def _run_background_history_import(user_id: str, chat_id, api_key: str, secret_key: str, bot):
    from services.history_import import import_trade_history
    set_bingx_credentials(api_key, secret_key)
    try:
        db = get_db()
        result = await import_trade_history(db, user_id)
        if not result.get('success'):
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Не удалось подтянуть историю сделок с биржи: {result.get('error')}. "
                     f"Можно попробовать позже: /importhistory"
            )
            return
        if result['imported'] == 0 and result['total_found'] == 0:
            await bot.send_message(
                chat_id=chat_id,
                text="ℹ️ Закрытых сделок в истории на бирже не найдено (или они старше 3 месяцев / "
                     "по редко торгуемым парам, которые не входят в проверенный список — /importhistory "
                     "можно повторить позже)."
            )
            return
        await bot.send_message(
            chat_id=chat_id,
            text=f"📥 История подтянута: {result['imported']} новых сделок "
                 f"(уже было: {result['skipped']}, найдено на бирже: {result['total_found']})."
        )
    except Exception as e:
        logger.error(f"_run_background_history_import: ошибка для {user_id}: {e}")
    finally:
        clear_bingx_credentials()


async def importhistory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручной повтор импорта истории (см. handle_awaiting_bingx_secret —
    авто-запускается один раз сразу после /setkeys). Доступен любому
    авторизованному пользователю — использует уже установленные для этого
    запроса ключи (middleware, core/user_context.py), а не только владельцу,
    в отличие от старой временной bot.py:restore_history_command (там же
    был баг: читала result['orders'] у ответа с ключом 'trades', и
    allOrders в принципе не даёт entry/exit цену на позицию — см.
    services/bingx_api.py:get_position_history)."""
    if not await require_auth(update, context):
        return
    from services.history_import import import_trade_history
    db = get_db()
    user_id = get_current_user_id(context)
    msg = await update.message.reply_text(
        "⏳ Запрашиваю историю закрытых позиций с биржи (может занять до ~15 секунд)..."
    )
    result = await import_trade_history(db, user_id)
    if not result.get('success'):
        await msg.edit_text(f"❌ Не удалось получить историю: {result.get('error')}")
        return
    await msg.edit_text(
        f"✅ Импортировано: {result['imported']}, уже было: {result['skipped']} "
        f"(найдено на бирже: {result['total_found']})."
    )
