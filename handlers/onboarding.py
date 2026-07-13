"""
handlers/onboarding.py
Мультитенантность, Этап 2 (см. MULTITENANCY_MIGRATION_PLAN.md) — привязка
пользователем своих API-ключей биржи (только чтение) + валидация реальным
запросом к бирже перед сохранением (зашифрованно, services/crypto_utils.py).

/setkeys открыт независимо от подписки: привязка ключей — часть онбординга,
который логически предшествует использованию платных функций (Этап 4,
Crypto Pay, ещё не подключён); сами торговые/AI-хендлеры уже гейтятся
require_auth() (Этап 3), так что открытый /setkeys не даёт доступа ни к
чему платному сам по себе.

Через Exchange Adapter Layer (services/exchanges/) — задача от 13.07.2026
("мультибиржевость обязательна") добавила реальный выбор биржи прямо в
/setkeys (BingX/Bybit/Binance/MEXC — OKX не реализован, см.
services/exchanges/registry.py) вместо прежнего жёсткого BingX. Инструкции
по получению ключа (_INSTRUCTIONS_BY_EXCHANGE) — единственное, что реально
разное между биржами на этом экране; остальной путь (валидация, шифрование,
сохранение) уже был написан exchange-agnostic на предыдущем этапе.
"""

import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes

from core.container import get_db
from core.keyboards import cancel_keyboard, main_menu_keyboard, exchange_choice_keyboard, EXCHANGE_LABELS
from core.user_context import get_current_user_id, require_auth
from services.exchange_api import validate_keys, set_current_exchange, clear_current_exchange

logger = logging.getLogger(__name__)

_INSTRUCTIONS_BY_EXCHANGE = {
    'bingx': (
        "🔑 Привяжем твои BingX API-ключи.\n\n"
        "⚠️ ВАЖНО: создавай ключ с правами ТОЛЬКО НА ЧТЕНИЕ (Read-Only). "
        "НЕ включай торговлю и вывод средств — боту для аналитики это не нужно, "
        "а тебе так безопаснее.\n\n"
        "Как получить: BingX → Аккаунт → API Management → Create API Key → "
        "оставь только разрешение «Read».\n\n"
        "Пришли API Key (или «отмена»):"
    ),
    'bybit': (
        "🔑 Привяжем твои Bybit API-ключи.\n\n"
        "⚠️ ВАЖНО: создавай ключ с правами ТОЛЬКО НА ЧТЕНИЕ (без Trade/Withdraw).\n\n"
        "Как получить: Bybit → Profile → API → Create New Key → System-generated API Keys → "
        "оставь только разрешения на чтение (Read-Only), Unified Trading Account.\n\n"
        "Пришли API Key (или «отмена»):"
    ),
    'binance': (
        "🔑 Привяжем твои Binance API-ключи.\n\n"
        "⚠️ ВАЖНО: создавай ключ с правами ТОЛЬКО НА ЧТЕНИЕ (без Enable Trading/Withdrawals).\n\n"
        "Как получить: Binance → Account → API Management → Create API → "
        "включи только Enable Reading, для USDT-M Futures аккаунта.\n\n"
        "Пришли API Key (или «отмена»):"
    ),
    'mexc': (
        "🔑 Привяжем твои MEXC API-ключи.\n\n"
        "⚠️ ВАЖНО: создавай ключ с правами ТОЛЬКО НА ЧТЕНИЕ (без Trade/Withdraw).\n\n"
        "Как получить: MEXC → Account → API Management → Create API → "
        "оставь только права на чтение, для Futures-аккаунта.\n\n"
        "Пришли API Key (или «отмена»):"
    ),
}


async def setkeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'awaiting_exchange_choice'
    context.user_data.pop('pending_bingx_api_key', None)
    context.user_data.pop('pending_exchange', None)
    await update.message.reply_text(
        "Какую биржу подключаем?",
        reply_markup=exchange_choice_keyboard()
    )


async def handle_awaiting_exchange_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    exchange = EXCHANGE_LABELS.get(text)
    if not exchange:
        await update.message.reply_text(
            "Выбери биржу на клавиатуре 👇", reply_markup=exchange_choice_keyboard()
        )
        return
    context.user_data['pending_exchange'] = exchange
    context.user_data['state'] = 'awaiting_bingx_key'
    await update.message.reply_text(
        _INSTRUCTIONS_BY_EXCHANGE[exchange], reply_markup=cancel_keyboard()
    )


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
    exchange = context.user_data.get('pending_exchange', 'bingx')
    context.user_data['state'] = None
    context.user_data.pop('pending_bingx_api_key', None)
    context.user_data.pop('pending_exchange', None)

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
    result = await validate_keys(exchange, api_key, secret_key)

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
        db.set_exchange(user_id, exchange)
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

    if context.user_data.get('guided_onboarding'):
        # Управляемый онбординг (задача от 12.07.2026 — /start должен
        # проводить по шагам, не просто перечислять команды в /help):
        # после привязки ключей сразу продолжаем риск-профилем, флаг
        # снимается в handlers/risk_profile.py:handle_awaiting_risk_goal.
        from handlers.risk_profile import riskprofile_command
        await update.effective_chat.send_message("Осталось настроить риск-профиль — 4 коротких шага.")
        await riskprofile_command(update, context)
    else:
        await update.effective_chat.send_message("Главное меню:", reply_markup=main_menu_keyboard())

    # Фоново, чтобы не задерживать ответ пользователю — /riskscore (Этап
    # "персональная модель риска") требует минимум 5 закрытых сделок,
    # ждать их накопления через обычный периодический sync могло бы занять
    # недели. Ключи передаём явно (set_current_exchange/clear в finally),
    # а не полагаемся на ambient contextvar — тот к этому моменту уже
    # сброшен обратно middleware'ом (core/user_context.py) для этого же
    # запроса (validate_keys() сама восстанавливает прежнее значение через
    # token/reset после проверки, см. services/bingx_api.py).
    asyncio.create_task(
        _run_background_history_import(user_id, update.effective_chat.id, exchange, api_key, secret_key, context.bot)
    )


async def _run_background_history_import(user_id: str, chat_id, exchange: str, api_key: str, secret_key: str, bot):
    from services.history_import import import_trade_history
    set_current_exchange(exchange, api_key, secret_key)
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
        clear_current_exchange()


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
