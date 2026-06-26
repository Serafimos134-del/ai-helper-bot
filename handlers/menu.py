"""
handlers/menu.py
Main menu handler — routes button presses to appropriate handlers.
"""

from telegram import Update
from telegram.ext import ContextTypes
from core.container import get_db
from core.keyboards import (
    main_menu_keyboard, trading_menu_keyboard, ai_menu_keyboard, cancel_keyboard
)
from handlers.trading import show_balance, show_last_trades, show_stats, show_ai_analysis
from handlers.ai import (
    show_market_overview, show_trends, show_journal_analysis,
    consilium_menu, consilium_open_positions, consilium_analyze_position,
    consilium_new_setup, consilium_process_setup,
)
from handlers.journal import show_journal
from handlers.system import show_help
from services.comment_manager import save_comment

import os
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# Button texts
BTN_TRADING = "📈 Trading"
BTN_AI = "🤖 AI"
BTN_JOURNAL = "📓 Журнал"
BTN_HELP = "ℹ️ Help"
BTN_BALANCE = "💰 Баланс"
BTN_LAST_TRADES = "📋 Последние сделки"
BTN_STATS = "📊 Статистика"
BTN_AI_ANALYSIS = "🧠 AI-анализ"
BTN_BACK = "🔙 Назад"
BTN_CANCEL = "❌ Отмена"
BTN_CONSILIUM = "🧠 Консилиум"
CONSILIUM_OPEN = "📂 Открытые сделки"
CONSILIUM_SETUP = "🎯 Новый сетап"
BTN_AI_MARKET = "🌐 Обзор рынка"
BTN_AI_TRENDS = "📊 Тренды"
BTN_AI_LEARN = "📊 Анализ журнала"

NAV_BUTTONS = {
    BTN_TRADING, BTN_AI, BTN_JOURNAL, BTN_HELP,
    BTN_BALANCE, BTN_LAST_TRADES, BTN_STATS, BTN_AI_ANALYSIS,
    BTN_BACK, BTN_CANCEL,
    BTN_AI_MARKET, BTN_AI_TRENDS, BTN_AI_LEARN,
    BTN_CONSILIUM, CONSILIUM_OPEN, CONSILIUM_SETUP,
    "🏠 *Главное меню*\nВыбери раздел:"
}


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != CHAT_ID:
        return
    text = update.message.text.strip()
    state = context.user_data.get('state')
    db = get_db()

    if text == BTN_CANCEL:
        if state == 'consilium_setup_input':
            context.user_data['state'] = None
            await update.message.reply_text("Отменено.", reply_markup=ai_menu_keyboard())
            return
        if state in ('entering_comment_inline', 'entering_exit_reason', 'entering_entry_reason'):
            context.user_data['state'] = None
            context.user_data.pop('comment_order_id', None)
            context.user_data.pop('entry_order_id', None)
            await update.message.reply_text("Отменено.", reply_markup=trading_menu_keyboard())
            return

    if state in ('entering_comment_inline', 'entering_exit_reason', 'entering_entry_reason'):
        if text in NAV_BUTTONS or text.startswith("🏠 *"):
            await update.message.reply_text("⚠️ Это навигационная кнопка, а не комментарий. Пожалуйста, введите текст комментария.", reply_markup=cancel_keyboard())
            return

    if state == 'entering_comment_inline':
        order_id = context.user_data.get('comment_order_id')
        if order_id:
            success = save_comment(order_id, text)
            if success:
                await update.message.reply_text(f"✅ Комментарий сохранён для сделки #{order_id}!", reply_markup=trading_menu_keyboard())
            else:
                await update.message.reply_text("❌ Сделка не найдена.", reply_markup=trading_menu_keyboard())
        context.user_data['state'] = None
        context.user_data.pop('comment_order_id', None)
        return

    if state == 'entering_exit_reason':
        trade_id = context.user_data.get('comment_order_id')
        if trade_id:
            db.update_trade_metrics(trade_id, exit_comment=text)
            await update.message.reply_text("✅ Вывод сохранён.", reply_markup=trading