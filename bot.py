import logging
import os
import json
import re
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from services.bingx_api import get_balance, get_open_positions, get_closed_orders, get_top_tickers, get_kline, get_ticker
from services.database import Database
from services.trading_stats import format_stats_message
from services.comment_manager import save_comment   # оставили для inline-комментариев
from services.auto_sync import sync_trades
from services.ai_trading import AITradingAnalyzer

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

ai_analyzer = AITradingAnalyzer()
db = Database()

# ─── Тексты кнопок ──────────────────────────────────────────────────────────

BTN_TRADING = "📈 Trading"
BTN_AI = "🤖 AI"
BTN_JOURNAL = "📓 Журнал"
BTN_HELP = "ℹ️ Help"

BTN_BALANCE = "💰 Баланс"
BTN_LAST_TRADES = "📋 Последние сделки"
BTN_STATS = "📊 Статистика"
BTN_AI_EVALUATION = "🤖 Оценка сделки"       # вместо "Комментарий"
BTN_AI_ANALYSIS = "🧠 AI-анализ"

BTN_BACK = "🔙 Назад"
BTN_CANCEL = "❌ Отмена"

# AI-меню
BTN_AI_OPEN_ANALYSIS = "📈 Анализ открытых сделок"
BTN_AI_ASK = "💬 Задать вопрос AI"
BTN_AI_MARKET = "🌐 Обзор рынка"
BTN_AI_TRENDS = "📊 Тренды"
BTN_AI_LEARN = "📊 Анализ журнала"

# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_TRADING],
            [BTN_AI, BTN_JOURNAL],
            [BTN_HELP],
        ],
        resize_keyboard=True
    )

def trading_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_BALANCE, BTN_LAST_TRADES],
            [BTN_STATS, BTN_AI_EVALUATION],   # изменено
            [BTN_AI_ANALYSIS],
            [BTN_BACK],
        ],
        resize_keyboard=True
    )

def ai_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_AI_OPEN_ANALYSIS],
            [BTN_AI_ASK],
            [BTN_AI_MARKET, BTN_AI_TRENDS],
            [BTN_AI_LEARN],
            [BTN_BACK],
        ],
        resize_keyboard=True
    )

def open_positions_keyboard(positions):
    buttons = [[f"{p.get('symbol', '?')} {p.get('side', '')}"] for p in positions]
    buttons.append([BTN_BACK])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# Убрали comment_select_keyboard и cancel_keyboard (оставили cancel_keyboard для inline)

def cancel_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)

# ─── Хендлеры ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "👋 *AI Helper Bot*\n\n"
        "Твой помощник трейдера.\n"
        "Отслеживаю сделки, веду дневник, считаю статистику.\n\n"
        "Используй кнопки меню 👇"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu_keyboard())

async def show_balance(update: Update):
    msg = await update.message.reply_text("⏳ Получаю баланс...")
    result = get_balance()
    if result.get('success'):
        text = (
            f"💰 *Баланс аккаунта*\n\n"
            f"📊 Эквити: ${result['equity']:.2f} USDT\n"
            f"✅ Доступно: ${result['available']:.2f} USDT\n"
            f"🔒 Использовано: ${result['used_margin']:.2f} USDT\n"
            f"📈 Нереализованный PNL: ${result['unrealized_pnl']:+.2f} USDT"
        )
    else:
        text = f"❌ Ошибка получения баланса:\n`{result.get('error', 'Неизвестная ошибка')}`"
    await msg.edit_text(text, parse_mode='Markdown')

async def show_last_trades(update: Update):
    msg = await update.message.reply_text("⏳ Загружаю сделки...")

    open_trades = db.get_open_trades()
    closed_trades = db.get_closed_trades(limit=15)

    lines = ["📋 *Последние сделки*\n"]

    if open_trades:
        lines.append("🔓 *Открытые позиции:*")
        for t in open_trades:
            pnl = float(t.get('unrealized_pnl', 0))
            if pnl > 0:
                emoji = "🟢"
            elif pnl < 0:
                emoji = "🔴"
            else:
                emoji = "⚪"
            lines.append(
                f"{emoji} {t.get('symbol')} {t.get('side')} | "
                f"Вход: ${float(t.get('entry_price', 0)):.4f} | "
                f"PNL: ${pnl:+.2f}"
            )
    else:
        lines.append("🔓 Открытых позиций нет")

    keyboard = []
    if closed_trades:
        lines.append("\n✅ *Последние закрытые (нажми для деталей):*")
        for t in reversed(closed_trades):
            pnl = float(t.get('realized_pnl', 0))
            if pnl > 0:
                emoji = "✅"
            elif pnl < 0:
                emoji = "❌"
            else:
                emoji = "➖"
            label = f"{emoji} {t['symbol']} {pnl:+.2f}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"detail_{t['id']}")])
    else:
        lines.append("\n✅ Закрытых сделок нет")

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await msg.edit_text("\n".join(lines), parse_mode='Markdown', reply_markup=reply_markup)


async def show_stats(update: Update):
    msg = await update.message.reply_text("⏳ Считаю статистику...")
    stats = db.get_stats()
    text = format_stats_message(stats)
    await msg.edit_text(text, parse_mode='Markdown')

async def show_ai_analysis(update: Update):
    msg = await update.message.reply_text("🤖 Анализирую...")
    text = ai_analyzer.analyze()
    await msg.edit_text(text, parse_mode='Markdown')

# ... (остальные функции show_market_overview, show_trends, show_journal, show_journal_analysis и т.д. оставлены без изменений – они есть в предыдущем полном bot.py)

# ── Новая функция: выбор сделки для AI-оценки ──
async def show_trades_for_evaluation(update: Update):
    trades = db.get_closed_trades(limit=15)
    if not trades:
        await update.message.reply_text("Нет закрытых сделок для оценки.")
        return
    keyboard = []
    for t in reversed(trades):
        label = f"{t['symbol']} {t['side']} PNL: {t['realized_pnl']:.2f}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"eval_{t['id']}")])
    await update.message.reply_text(
        "🤖 *Выберите сделку для AI-оценки:*",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ── AI-оценка одной сделки ──
async def generate_ai_review(query, trade_id):
    trade = db.find_trade_by_id(trade_id)
    if not trade:
        await query.edit_message_text("❌ Сделка не найдена.")
        return
    prompt = (
        f"Дай краткую оценку сделке (2-3 предложения): что хорошо, что плохо, оценка от 1 до 10.\n"
        f"Символ: {trade['symbol']}, сторона: {trade['side']}, вход: {trade['entry_price']}, "
        f"выход: {trade['exit_price']}, плечо: {trade.get('leverage', 1)}, PNL: {trade['realized_pnl']:.2f}.\n"
        f"Причина входа: {trade.get('entry_comment', 'не указана')}."
    )
    review = ai_analyzer.analyze_raw(prompt)
    # Сохраняем оценку в базу
    db.add_comment(trade_id, review)  # временно в comment, можно отдельное поле
    await query.edit_message_text(f"🤖 *AI-оценка сделки #{trade_id}:*\n\n{review}", parse_mode='Markdown')

# ── Обработчик inline-кнопок (расширен) ──
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("comment_"):
        trade_id = int(data.split("_")[1])
        context.user_data['comment_order_id'] = trade_id
        context.user_data['state'] = 'entering_comment_inline'
        await query.edit_message_text(
            f"✏️ *Напишите комментарий* к сделке #{trade_id}:",
            parse_mode='Markdown',
            reply_markup=cancel_keyboard()
        )
    elif data.startswith("detail_"):
        trade_id = int(data.split("_")[1])
        trade = db.find_trade_by_id(trade_id)
        if trade:
            detail_text = (
                f"📊 *Детали сделки #{trade_id}*\n\n"
                f"Символ: {trade['symbol']}\n"
                f"Сторона: {trade['side']}\n"
                f"Вход: ${trade['entry_price']:.4f}\n"
                f"Выход: ${trade['exit_price']:.4f}\n"
                f"Объём: {trade['quantity']}\n"
                f"Плечо: {trade.get('leverage', 1)}x\n"
                f"PNL: ${trade['realized_pnl']:.2f}\n"
                f"Комментарий: {trade.get('comment', '—')}\n"
                f"Закрыта: {trade.get('close_time') or trade.get('closed_at', '—')}"
            )
            detail_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Добавить комментарий", callback_data=f"comment_{trade_id}")]
            ])
            await query.edit_message_text(detail_text, parse_mode='Markdown', reply_markup=detail_keyboard)
        else:
            await query.edit_message_text("❌ Сделка не найдена.")
    elif data.startswith("eval_"):
        trade_id = int(data.split("_")[1])
        await generate_ai_review(query, trade_id)
    elif data.startswith("entry_reason_"):
        # ... (будет добавлено в следующей итерации)
        pass
    elif data.startswith("exit_reason_"):
        # ... 
        pass
    elif data.startswith("ai_review_"):
        trade_id = int(data.split("_")[2])
        await generate_ai_review(query, trade_id)
    elif data == "skip_comment":
        await query.edit_message_text("Запись сохранена без комментария.")

# ── Главный обработчик сообщений (меню и состояния) ──
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    state = context.user_data.get('state')

    # Состояние: ввод комментария после inline-кнопки
    if state == 'entering_comment_inline':
        if text == BTN_CANCEL:
            context.user_data['state'] = None
            context.user_data.pop('comment_order_id', None)
            await update.message.reply_text("Отменено.", reply_markup=trading_menu_keyboard())
            return
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

    # Состояние: вопрос AI (оставлено)
    if state == 'asking_ai':
        if text == BTN_CANCEL:
            context.user_data['state'] = None
            await update.message.reply_text("Отменено.", reply_markup=ai_menu_keyboard())
            return
        # ... полный код asking_ai как раньше
        return

    # Состояние: выбор позиции для AI-анализа (оставлено)
    if state == 'choosing_position':
        # ... полный код choosing_position как раньше
        return

    # Навигация по меню
    if text == BTN_TRADING:
        await update.message.reply_text("📈 *Trading*\nВыбери действие:", parse_mode='Markdown', reply_markup=trading_menu_keyboard())
    elif text == BTN_AI:
        await update.message.reply_text("🤖 *AI-Ассистент*\nВыбери, что хочешь проанализировать:", parse_mode='Markdown', reply_markup=ai_menu_keyboard())
    elif text == BTN_JOURNAL:
        await show_journal(update)
    elif text == BTN_HELP:
        await show_help(update)
    elif text == BTN_BACK:
        await update.message.reply_text("🏠 *Главное меню*\nВыбери раздел:", parse_mode='Markdown', reply_markup=main_menu_keyboard())
    elif text == BTN_BALANCE:
        await show_balance(update)
    elif text == BTN_LAST_TRADES:
        await show_last_trades(update)
    elif text == BTN_STATS:
        await show_stats(update)
    elif text == BTN_AI_EVALUATION:          # <-- кнопка оценки
        await show_trades_for_evaluation(update)
    elif text == BTN_AI_ANALYSIS:
        await show_ai_analysis(update)
    elif text == BTN_AI_OPEN_ANALYSIS:
        await start_open_position_analysis(update, context)
    elif text == BTN_AI_ASK:
        context.user_data['state'] = 'asking_ai'
        await update.message.reply_text(
            "💬 *Задай вопрос AI-тренеру:*\n\n"
            "Например: «Стоит ли сейчас открывать лонг по ETH?» или «Как улучшить дисциплину?»",
            parse_mode='Markdown',
            reply_markup=cancel_keyboard()
        )
    elif text == BTN_AI_MARKET:
        await show_market_overview(update)
    elif text == BTN_AI_TRENDS:
        await show_trends(update)
    elif text == BTN_AI_LEARN:
        await show_journal_analysis(update)
    else:
        await update.message.reply_text("Используй кнопки меню 👇", reply_markup=main_menu_keyboard())

# ── Остальные функции (ai_fix, auto_sync_job, update_pinned_status, sync_command) остаются без изменений ──
# ... (добавьте их из предыдущей полной версии)

# ─── Запуск ───────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан! Проверь .env файл.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('sync', sync_command))
    app.add_handler(CommandHandler('ai_fix', ai_fix_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.job_queue.run_repeating(auto_sync_job, interval=60, first=10)
    app.job_queue.run_repeating(update_pinned_status, interval=300, first=30)

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()