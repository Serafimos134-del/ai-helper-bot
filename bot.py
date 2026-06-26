"""
bot.py
Main entry point — thin launcher.
"""

import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from services.database import init_db
from core.container import get_db
from core.router import setup_router
from core.scheduler import setup_scheduler

from handlers.system import start, health_command, sync_command, status_command, ai_fix_command
from handlers.menu import menu_handler

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')


def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан! Проверь .env файл.")
    init_db()
    db = get_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('sync', sync_command))
    app.add_handler(CommandHandler('status', status_command))
    app.add_handler(CommandHandler('ai_fix', ai_fix_command))
    app.add_handler(CommandHandler('health', health_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))
    setup_router(app)
    setup_scheduler(app, db, CHAT_ID)
    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()