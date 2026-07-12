"""
bot.py
Main entry point — thin launcher with global error handler.
Phase 1 Cleanup Architecture: removed /analyze and /calc from public commands.
"""

import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, TypeHandler, filters

from services.database import init_db, Database
from core.container import get_db
from core.router import setup_router
from core.scheduler import setup_scheduler
from core.user_context import resolve_user_context

from handlers.system import (
    start, health_command, sync_command, status_command,
    ai_fix_command, test_behavior_command,
    setidea_command,                      # internal/admin
    debug_positions_command,              # временная diagnostic-команда, см. handlers/system.py
    notifications_command,
)
from handlers.ai import show_coach
from handlers.menu import menu_handler
from handlers.onboarding import setkeys_command
from handlers.subscription import subscribe_command
from handlers.risk_profile import riskprofile_command, riskscore_command

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID', '')
BINGX_API_KEY    = os.getenv('BINGX_API_KEY', '')
BINGX_SECRET_KEY = os.getenv('BINGX_SECRET_KEY', '')


async def error_handler(update: object, context) -> None:
    """Глобальный обработчик ошибок — логирует и не даёт боту упасть."""
    logger.error("Ошибка при обработке обновления:", exc_info=context.error)


async def restore_history_command(update: Update, context):
    """Временная команда для восстановления истории закрытых сделок из BingX API."""
    if str(update.effective_chat.id) != CHAT_ID:
        return
    msg = await update.message.reply_text("⏳ Запрашиваю историю закрытых сделок из BingX...")
    try:
        from services.bingx_api import get_closed_orders
        result = await get_closed_orders()
        if not result.get('success'):
            await msg.edit_text(f"❌ Ошибка API: {result.get('error')}")
            return
        orders = result.get('orders', [])
        if not orders:
            await msg.edit_text("Нет закрытых сделок в истории.")
            return

        db       = Database()
        restored = 0
        skipped  = 0
        for order in orders:
            oid = order.get('orderId')
            if not oid:
                continue
            existing = db._execute("SELECT id FROM closed_trades WHERE orderId = ?", (oid,)).fetchone()
            if existing:
                skipped += 1
                continue
            side = 'LONG' if str(order.get('side', '')).upper() in ('BUY', 'LONG') else 'SHORT'
            trade = {
                'orderId':      oid,
                'symbol':       order.get('symbol'),
                'side':         side,
                'entry_price':  float(order.get('entryPrice', 0)),
                'exit_price':   float(order.get('exitPrice', 0)),
                'quantity':     abs(float(order.get('positionAmt', order.get('size', 0)))),
                'realized_pnl': float(order.get('realizedPnl', 0)),
                'leverage':     float(order.get('leverage', 1)),
                'open_time':    order.get('openTime'),
                'close_time':   order.get('closeTime'),
                'comment':      ''
            }
            try:
                db.add_closed_trade(trade)
                restored += 1
            except Exception as e:
                logger.error(f"Ошибка вставки {oid}: {e}")

        await msg.edit_text(f"✅ Восстановлено: {restored}, пропущено (уже есть): {skipped}")
    except Exception as e:
        logger.error(f"Ошибка восстановления истории: {e}")
        await msg.edit_text(f"❌ Ошибка: {e}")


def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан! Проверь .env файл.")
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        raise ValueError(
            "BINGX_API_KEY/BINGX_SECRET_KEY не заданы! Без них запросы к BingX "
            "уйдут с пустым секретом и будут молча возвращать ошибки авторизации. "
            "Проверь .env файл."
        )
    init_db()
    db  = get_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Глобальный обработчик ошибок
    app.add_error_handler(error_handler)

    # Мультитенантность (см. MULTITENANCY_MIGRATION_PLAN.md): резолвит
    # пользователя и его BingX-ключи ДО всех остальных хендлеров —
    # group=-1 обрабатывается раньше group=0 (публичные команды/меню).
    app.add_handler(TypeHandler(Update, resolve_user_context), group=-1)

    # Публичные команды
    app.add_handler(CommandHandler('start',           start))
    app.add_handler(CommandHandler('sync',            sync_command))
    app.add_handler(CommandHandler('status',          status_command))
    app.add_handler(CommandHandler('health',          health_command))
    app.add_handler(CommandHandler('coach',           show_coach))
    app.add_handler(CommandHandler('setkeys',         setkeys_command))
    app.add_handler(CommandHandler('subscribe',       subscribe_command))
    app.add_handler(CommandHandler('notifications',   notifications_command))
    app.add_handler(CommandHandler('riskprofile',     riskprofile_command))
    app.add_handler(CommandHandler('riskscore',       riskscore_command))

    # Debug / admin (можно оставить для тестов)
    app.add_handler(CommandHandler('ai_fix',          ai_fix_command))
    app.add_handler(CommandHandler('test_behavior',   test_behavior_command))
    app.add_handler(CommandHandler('setidea',         setidea_command))
    app.add_handler(CommandHandler('restore_history', restore_history_command))
    app.add_handler(CommandHandler('debug_positions', debug_positions_command))

    # Основное меню
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    setup_router(app)
    setup_scheduler(app, db, CHAT_ID)
    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()