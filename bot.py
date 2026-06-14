import logging
import os
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from services.bingx_api import get_balance, get_open_positions, get_closed_orders
from services.trading_storage import (
    get_open_trades,
    get_closed_trades,
)
from services.trading_stats import calculate_stats, format_stats_message
from services.comment_manager import get_trades_for_comment, save_comment
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

# ─── Тексты кнопок ──────────────────────────────────────────────────────────

BTN_TRADING = "📈 Trading"
BTN_AI = "🤖 AI"
BTN_SHORTS = "🎬 Shorts"
BTN_HELP = "ℹ️ Help"

BTN_BALANCE = "💰 Баланс"
BTN_LAST_TRADES = "📋 Последние сделки"
BTN_STATS = "📊 Статистика"
BTN_COMMENT = "✏️ Комментарий"
BTN_AI_ANALYSIS = "🧠 AI-анализ"

BTN_BACK = "🔙 Назад"
BTN_CANCEL = "❌ Отмена"

# AI-меню
BTN_AI_OPEN_ANALYSIS = "📈 Анализ открытых сделок"
BTN_AI_ASK = "💬 Задать вопрос AI"
BTN_AI_MARKET = "🌐 Обзор рынка"
BTN_AI_TRENDS = "📊 Тренды"

# ─── Клавиатуры (Reply, снизу) ────────────────────────────────────────────────

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_TRADING],
            [BTN_AI, BTN_SHORTS],
            [BTN_HELP],
        ],
        resize_keyboard=True
    )


def trading_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_BALANCE, BTN_LAST_TRADES],
            [BTN_STATS, BTN_COMMENT],
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
            [BTN_BACK],
        ],
        resize_keyboard=True
    )


def comment_select_keyboard(trades):
    buttons = [[t['label']] for t in trades[:8]]
    buttons.append([BTN_CANCEL])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def cancel_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)


# ─── Хендлеры ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — показать главное меню."""
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

    open_trades = get_open_trades()
    closed_trades = get_closed_trades()[-5:]

    lines = ["📋 *Последние сделки*\n"]

    if open_trades:
        lines.append("🔓 *Открытые позиции:*")
        for t in open_trades:
            pnl = float(t.get('unrealizedPnl', 0))
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{emoji} {t.get('symbol')} {t.get('side')} | "
                f"Вход: ${float(t.get('entryPrice', 0)):.4f} | "
                f"PNL: ${pnl:+.2f}"
            )
    else:
        lines.append("🔓 Открытых позиций нет")

    if closed_trades:
        lines.append("\n✅ *Последние закрытые:*")
        for t in reversed(closed_trades):
            pnl = float(t.get('realizedPnl', t.get('pnl', 0)))
            emoji = "✅" if pnl >= 0 else "❌"
            comment = f"\n   💬 {t['comment']}" if t.get('comment') else ""
            lines.append(
                f"{emoji} {t.get('symbol')} | PNL: ${pnl:+.2f}{comment}"
            )
    else:
        lines.append("\n✅ Закрытых сделок нет")

    text = "\n".join(lines)
    await msg.edit_text(text, parse_mode='Markdown')


async def show_stats(update: Update):
    msg = await update.message.reply_text("⏳ Считаю статистику...")
    stats = calculate_stats()
    text = format_stats_message(stats)
    await msg.edit_text(text, parse_mode='Markdown')


async def start_comment_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = get_trades_for_comment()
    if not trades:
        await update.message.reply_text(
            "📝 Нет сделок для комментария.\nСначала нужно открыть или закрыть позицию.",
            reply_markup=trading_menu_keyboard()
        )
        return

    context.user_data['comment_trades'] = {t['label']: t['orderId'] for t in trades[:8]}
    context.user_data['state'] = 'choosing_trade'

    await update.message.reply_text(
        "✏️ *Выбери сделку для комментария:*",
        parse_mode='Markdown',
        reply_markup=comment_select_keyboard(trades)
    )


async def show_ai_analysis(update: Update):
    msg = await update.message.reply_text("🤖 Анализирую...")
    text = ai_analyzer.analyze()
    await msg.edit_text(text, parse_mode='Markdown')


async def show_help(update: Update):
    text = (
        "ℹ️ *Помощь*\n\n"
        "📈 *Trading* — работа со сделками:\n"
        "  • 💰 Баланс — текущий баланс BingX\n"
        "  • 📋 Последние сделки — открытые и закрытые позиции\n"
        "  • 📊 Статистика — Win Rate, PNL и др.\n"
        "  • ✏️ Комментарий — дневник трейдера\n"
        "  • 🧠 AI-анализ — анализ торговли\n\n"
        "🔄 Бот автоматически проверяет новые сделки каждые 60 секунд.\n\n"
        "📌 *Команды:*\n"
        "/start — главное меню\n"
        "/sync — ручная синхронизация"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu_keyboard())


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик текстовых сообщений (включает навигацию по меню)."""
    text = update.message.text.strip()
    state = context.user_data.get('state')

    # ── Состояние: ожидание комментария к сделке ──
    if state == 'entering_comment':
        if text == BTN_CANCEL:
            context.user_data['state'] = None
            context.user_data.pop('comment_order_id', None)
            await update.message.reply_text(
                "Отменено.",
                reply_markup=trading_menu_keyboard()
            )
            return

        order_id = context.user_data.get('comment_order_id')
        if order_id:
            success = save_comment(order_id, text)
            if success:
                await update.message.reply_text(
                    f"✅ Комментарий сохранён для сделки `{order_id}`!",
                    parse_mode='Markdown',
                    reply_markup=trading_menu_keyboard()
                )
            else:
                await update.message.reply_text(
                    "❌ Сделка не найдена. Попробуй снова.",
                    reply_markup=trading_menu_keyboard()
                )
        context.user_data['state'] = None
        context.user_data.pop('comment_order_id', None)
        return

    # ── Состояние: выбор сделки для комментария ──
    if state == 'choosing_trade':
        if text == BTN_CANCEL:
            context.user_data['state'] = None
            context.user_data.pop('comment_trades', None)
            await update.message.reply_text(
                "Отменено.",
                reply_markup=trading_menu_keyboard()
            )
            return

        trades_map = context.user_data.get('comment_trades', {})
        order_id = trades_map.get(text)
        if order_id:
            context.user_data['comment_order_id'] = order_id
            context.user_data['state'] = 'entering_comment'
            await update.message.reply_text(
                f"✏️ *Напиши комментарий* к сделке `{order_id}`:\n\n"
                "Например:\n"
                "• Почему вошел\n"
                "• Что пошло не так\n"
                "• Что узнал из этой сделки",
                parse_mode='Markdown',
                reply_markup=cancel_keyboard()
            )
        else:
            await update.message.reply_text(
                "Выбери сделку из списка кнопок 👇",
                reply_markup=comment_select_keyboard(
                    [{'label': k, 'orderId': v} for k, v in trades_map.items()]
                )
            )
        return

    # ── Состояние: ожидание вопроса для AI ──
    if state == 'asking_ai':
        if text == BTN_CANCEL:
            context.user_data['state'] = None
            await update.message.reply_text(
                "Отменено.",
                reply_markup=ai_menu_keyboard()
            )
            return

        context.user_data['state'] = None
        msg = await update.message.reply_text("🤖 Думаю...")
        answer = ai_analyzer.analyze_raw(text)
        await msg.edit_text(f"💬 *Ответ AI:*\n\n{answer}", parse_mode='Markdown', reply_markup=ai_menu_keyboard())
        return

    # ── Навигация по меню ──
    if text == BTN_TRADING:
        await update.message.reply_text(
            "📈 *Trading*\nВыбери действие:",
            parse_mode='Markdown',
            reply_markup=trading_menu_keyboard()
        )

    elif text == BTN_AI:
        await update.message.reply_text(
            "🤖 *AI-Ассистент*\nВыбери, что хочешь проанализировать:",
            parse_mode='Markdown',
            reply_markup=ai_menu_keyboard()
        )

    elif text == BTN_SHORTS:
        await update.message.reply_text(
            "🎬 *Shorts*\n\nРаздел в разработке.",
            parse_mode='Markdown',
            reply_markup=main_menu_keyboard()
        )

    elif text == BTN_HELP:
        await show_help(update)

    elif text == BTN_BACK:
        await update.message.reply_text(
            "🏠 *Главное меню*\nВыбери раздел:",
            parse_mode='Markdown',
            reply_markup=main_menu_keyboard()
        )

    elif text == BTN_BALANCE:
        await show_balance(update)

    elif text == BTN_LAST_TRADES:
        await show_last_trades(update)

    elif text == BTN_STATS:
        await show_stats(update)

    elif text == BTN_COMMENT:
        await start_comment_flow(update, context)

    elif text == BTN_AI_ANALYSIS:
        await show_ai_analysis(update)

    elif text == BTN_AI_OPEN_ANALYSIS:
        await update.message.reply_text("🚧 В разработке (этап 5).", reply_markup=ai_menu_keyboard())

    elif text == BTN_AI_ASK:
        context.user_data['state'] = 'asking_ai'
        await update.message.reply_text(
            "💬 *Задай вопрос AI-тренеру:*\n\n"
            "Например: «Стоит ли сейчас открывать лонг по ETH?» или «Как улучшить дисциплину?»",
            parse_mode='Markdown',
            reply_markup=cancel_keyboard()
        )

    elif text == BTN_AI_MARKET:
        await update.message.reply_text("🚧 В разработке (этап 4).", reply_markup=ai_menu_keyboard())

    elif text == BTN_AI_TRENDS:
        await update.message.reply_text("🚧 В разработке (этап 4).", reply_markup=ai_menu_keyboard())

    else:
        await update.message.reply_text(
            "Используй кнопки меню 👇",
            reply_markup=main_menu_keyboard()
        )


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /sync — ручная синхронизация сделок."""
    msg = await update.message.reply_text("🔄 Синхронизирую сделки с BingX...")
    results = await sync_trades(context.bot, update.effective_chat.id)
    new_open = len(results.get('new_open', []))
    new_closed = len(results.get('new_closed', []))
    await msg.edit_text(
        f"✅ Синхронизация завершена!\n\n"
        f"🆕 Новых позиций: {new_open}\n"
        f"🔒 Закрыто позиций: {new_closed}"
    )


async def auto_sync_job(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача автосинхронизации."""
    if not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID не задан, авто-синхронизация пропущена")
        return
    try:
        await sync_trades(context.bot, CHAT_ID)
    except Exception as e:
        logger.error(f"Ошибка авто-синхронизации: {e}")


# ─── Запуск ───────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан! Проверь .env файл.")

    app = Application.builder().token(BOT_TOKEN).build()

    # Хендлеры команд
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('sync', sync_command))

    # Хендлер текста (вся навигация по меню теперь здесь)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    # Авто-синхронизация каждые 60 секунд
    app.job_queue.run_repeating(auto_sync_job, interval=60, first=10)

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
