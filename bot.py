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
    ContextTypes,
    filters,
)

from services.bingx_api import get_balance, get_open_positions, get_closed_orders, get_top_tickers, get_kline, get_ticker
from services.database import Database, init_db
from services.trading_stats import format_stats_message
from services.comment_manager import save_comment
from services.auto_sync import sync_trades
from services.ai_trading import AITradingAnalyzer
from core.keyboards import cancel_keyboard
from core.router import setup_router
from core.scheduler import setup_scheduler

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
BTN_AI_ANALYSIS = "🧠 AI-анализ"

BTN_BACK = "🔙 Назад"
BTN_CANCEL = "❌ Отмена"

# AI-меню (обновлено)
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
            [BTN_STATS, BTN_AI_ANALYSIS],
            [BTN_BACK],
        ],
        resize_keyboard=True
    )

def ai_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_CONSILIUM],
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

# ─── Хендлеры (только для владельца) ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != CHAT_ID:
        return
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
            if pnl > 0: emoji = "🟢"
            elif pnl < 0: emoji = "🔴"
            else: emoji = "⚪"
            lines.append(f"{emoji} {t.get('symbol')} {t.get('side')} | Вход: ${float(t.get('entry_price', 0)):.4f} | PNL: ${pnl:+.2f}")
    else:
        lines.append("🔓 Открытых позиций нет")
    keyboard = []
    if closed_trades:
        lines.append("\n✅ *Последние закрытые (нажми для деталей):*")
        for t in reversed(closed_trades):
            pnl = float(t.get('realized_pnl', 0))
            if pnl > 0: emoji = "✅"
            elif pnl < 0: emoji = "❌"
            else: emoji = "➖"
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

async def show_market_overview(update: Update):
    msg = await update.message.reply_text("🌐 Собираю данные рынка...")
    result = get_top_tickers(10)
    if not result.get('success') or not result.get('tickers'):
        await msg.delete()
        await update.message.reply_text(f"❌ Не удалось получить данные рынка: {result.get('error', 'нет данных')}", reply_markup=ai_menu_keyboard())
        return
    summary = []
    for t in result['tickers']:
        symbol = t.get('symbol', '')
        change = float(t.get('priceChangePercent', 0))
        volume = float(t.get('quoteVolume', 0))
        summary.append(f"{symbol}: изм {change:+.2f}%, объём {volume:,.0f}")
    prompt = (
        "Проанализируй рыночную ситуацию на основе данных топ-10 криптовалют по объёму за 24 часа:\n"
        + "\n".join(summary)
        + "\n\nТвой ответ должен содержать строго:\n"
        + "1. ОБЩИЙ НАСТРОЙ: (бычий/медвежий/нейтральный) — одним предложением\n"
        + "2. ТОП-3 МОНЕТЫ С СИЛЬНЕЙШИМ ДВИЖЕНИЕМ (рост и падение) — назови и возможные причины\n"
        + "3. ВОЗМОЖНЫЕ ТОЧКИ ВХОДА: любые две монеты из списка с кратким обоснованием\n\n"
        + "Будь конкретен, используй цифры из данных выше. Без философии и общих фраз."
    )
    try:
        analysis = ai_analyzer.analyze_raw(prompt)
    except Exception as e:
        analysis = f"Ошибка AI: {e}"
    try: await msg.delete()
    except Exception: pass
    await update.message.reply_text(f"🌐 Обзор рынка от AI\n\n{analysis[:3500]}", reply_markup=ai_menu_keyboard())

async def show_trends(update: Update):
    msg = await update.message.reply_text("📊 Анализирую тренды...")
    symbols = ["BTC-USDT", "ETH-USDT"]
    data_lines = []
    for sym in symbols:
        result = get_kline(sym, "1h", 24)
        klines = result.get('klines', [])
        if result.get('success') and len(klines) >= 2:
            try:
                closes = [float(k.get('close', k.get('c', 0))) for k in klines]
                highs = [float(k.get('high', k.get('h', 0))) for k in klines]
                lows = [float(k.get('low', k.get('l', 0))) for k in klines]
                first_close = closes[0]
                last_close = closes[-1]
                if first_close:
                    change = (last_close - first_close) / first_close * 100
                    data_lines.append(f"{sym}: изменение за 24ч {change:+.2f}%, максимум {max(highs):.2f}, минимум {min(lows):.2f}, текущая цена {last_close:.2f}")
            except (ValueError, TypeError, AttributeError):
                continue
    if not data_lines:
        try: await msg.delete()
        except Exception: pass
        await update.message.reply_text("❌ Не удалось получить данные по трендам.", reply_markup=ai_menu_keyboard())
        return
    prompt = (
        "Тренд-анализ на основе часовых свечей за 24 часа:\n"
        + "\n".join(data_lines)
        + "\n\nТвой ответ дай строго в формате:\n"
        + "1. BTC: тренд (восходящий/нисходящий/боковик), ключевые уровни поддержки и сопротивления на сегодня\n"
        + "2. ETH: аналогично\n"
        + "3. СИГНАЛ: если видишь явную точку входа по любой из монет — укажи направление, цену входа и стоп-лосс. Если явного сигнала нет — напиши «явного сигнала нет»\n\n"
        + "Кратко, без воды, используй цифры из данных выше."
    )
    try:
        analysis = ai_analyzer.analyze_raw(prompt)
    except Exception as e:
        analysis = f"Ошибка AI: {e}"
    try: await msg.delete()
    except Exception: pass
    await update.message.reply_text(f"📊 Тренды от AI\n\n{analysis[:3500]}", reply_markup=ai_menu_keyboard())

# ── Журнал сделок ──
async def show_journal(update: Update):
    msg = await update.message.reply_text("📓 Загружаю журнал...")
    trades = db.get_closed_trades(limit=500)
    if not trades:
        await msg.edit_text("Нет закрытых сделок для журнала.")
        return
    await msg.delete()
    chunk_size = 15
    chunks = [trades[i:i+chunk_size] for i in range(0, len(trades), chunk_size)]
    for idx, chunk in enumerate(chunks, 1):
        lines = [f"📓 *Журнал сделок (часть {idx}/{len(chunks)})*\n"]
        for t in reversed(chunk):
            symbol = t['symbol']
            side = t['side']
            entry = f"${t['entry_price']:.4f}"
            exit = f"${t['exit_price']:.4f}"
            pnl = float(t['realized_pnl'])
            if pnl > 0: emoji = "✅"
            elif pnl < 0: emoji = "❌"
            else: emoji = "➖"
            volume = t['quantity']
            leverage = t.get('leverage', 1)
            stop = f"${t['stop_loss']:.4f}" if t.get('stop_loss') else "—"
            take = f"${t['take_profit']:.4f}" if t.get('take_profit') else "—"
            open_time = t.get('open_time') or "—"
            close_time = t.get('close_time') or t.get('closed_at') or "—"
            entry_comment = t.get('entry_comment', '—')
            exit_comment = t.get('exit_comment', t.get('comment', '—'))
            ai_review = t.get('ai_review', '')
            holding = t.get('holding_minutes')
            duration_str = f"{holding} мин" if holding is not None else "—"
            market_trend = t.get('market_trend', '—')
            setup = t.get('setup_type', '—')
            line = (
                f"{emoji} *{symbol}* {side}\n"
                f"   Вход: {entry} | Выход: {exit}\n"
                f"   Объём: {volume} | Плечо: {leverage}x\n"
                f"   Стоп: {stop} | Тейк: {take}\n"
                f"   PNL: ${pnl:.2f}\n"
                f"   Длительность: {duration_str}\n"
                f"   Тренд рынка: {market_trend}\n"
                f"   Сетап: {setup}\n"
                f"   Открыта: {open_time}\n"
                f"   Закрыта: {close_time}\n"
                f"   Причина входа: {entry_comment}\n"
                f"   Вывод: {exit_comment}\n"
                + (f"   AI-оценка: {ai_review}\n" if ai_review else "")
                + "\n"
            )
            lines.append(line)
        text = "".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n... (обрезано)"
        await update.message.reply_text(text, parse_mode='Markdown')

# ── Анализ журнала (AI) ──
async def show_journal_analysis(update: Update):
    msg = await update.message.reply_text("🤖 Анализирую журнал сделок...")
    trades = db.get_closed_trades(limit=50)
    if not trades:
        await msg.edit_text("Нет закрытых сделок для анализа.")
        return
    data_for_ai = []
    for t in trades:
        data_for_ai.append({
            'symbol': t['symbol'],
            'side': t['side'],
            'entry_price': t['entry_price'],
            'exit_price': t['exit_price'],
            'pnl': t['realized_pnl'],
            'leverage': t.get('leverage', 1),
            'stop_loss': t.get('stop_loss'),
            'take_profit': t.get('take_profit'),
            'entry_comment': t.get('entry_comment', ''),
            'exit_comment': t.get('exit_comment', t.get('comment', ''))
        })
    trades_text = json.dumps(data_for_ai, ensure_ascii=False, indent=2)
    prompt = (
        "Проанализируй журнал сделок трейдера. "
        "Выдели повторяющиеся паттерны, главные ошибки в риск-менеджменте, "
        "психологические ловушки и сильные стороны. "
        "Дай конкретные рекомендации по улучшению стратегии и дисциплины.\n\n"
        f"Журнал сделок:\n{trades_text}"
    )
    answer = ai_analyzer.analyze_raw(prompt)
    await msg.edit_text(f"📊 *Анализ журнала:*\n\n{answer[:3500]}")

async def show_help(update: Update):
    text = (
        "ℹ️ *Помощь*\n\n"
        "📈 *Trading* — работа со сделками:\n"
        "  • 💰 Баланс — текущий баланс BingX\n"
        "  • 📋 Последние сделки — открытые и закрытые позиции\n"
        "  • 📊 Статистика — Win Rate, PNL и др.\n"
        "  • 🧠 AI-анализ — общий анализ портфеля\n"
        "  • 🧠 Консилиум — AI-анализ позиций и новых сетапов\n\n"
        "🔄 Синхронизация каждые 15 секунд.\n\n"
        "📌 *Команды:*\n"
        "/start — главное меню\n"
        "/sync — ручная синхронизация\n"
        "/ai\\_fix — AI-разбор серии убыточных сделок\n"
        "/health — состояние систем"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu_keyboard())

# ── Health check ──
async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != CHAT_ID:
        return
    msg = await update.message.reply_text("🩺 Проверяю здоровье систем...")
    status = []
    try:
        db.get_open_trades()
        status.append("🗄 База данных: 🟢")
    except Exception:
        status.append("🗄 База данных: 🔴")
    try:
        balance = get_balance()
        if balance.get('success'):
            status.append("📡 BingX API: 🟢")
        else:
            status.append(f"📡 BingX API: 🔴 ({balance.get('error', 'неизвестно')})")
    except Exception as e:
        status.append(f"📡 BingX API: 🔴 ({e})")
    if ai_analyzer.provider:
        try:
            test = ai_analyzer.provider.generate("ping")
            if test and "unavailable" not in test:
                status.append("🧠 AI-провайдер: 🟢")
            else:
                status.append("🧠 AI-провайдер: 🔴 (не отвечает)")
        except Exception as e:
            status.append(f"🧠 AI-провайдер: 🔴 ({e})")
    else:
        status.append("🧠 AI-провайдер: 🔴 (нет ключа)")
    if os.getenv('HTTP_PROXY') or os.getenv('HTTPS_PROXY'):
        status.append("🌐 Прокси: 🟢 (настроен)")
    else:
        status.append("🌐 Прокси: ⚪ (не используется)")
    await msg.edit_text("📊 *Health Check*\n\n" + "\n".join(status), parse_mode='Markdown')

# ── Главный обработчик сообщений ──
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != CHAT_ID:
        return
    text = update.message.text.strip()
    state = context.user_data.get('state')

    # Обработка кнопки Отмена для всех состояний
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

    # Обработка состояний комментариев
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
            await update.message.reply_text("✅ Вывод сохранён.", reply_markup=trading_menu_keyboard())
        context.user_data['state'] = None
        context.user_data.pop('comment_order_id', None)
        return

    if state == 'entering_entry_reason':
        order_id = context.user_data.get('entry_order_id')
        if order_id:
            db.update_open_trade_by_order_id(order_id, entry_comment=text)
            await update.message.reply_text("✅ Причина входа сохранена.", reply_markup=trading_menu_keyboard())
        context.user_data['state'] = None
        return

    # Новые состояния Консилиума
    if state == 'consilium_choose_position':
        await consilium_analyze_position(update, context)
        return
    if state == 'consilium_setup_input':
        await consilium_process_setup(update, context)
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
    elif text == BTN_AI_ANALYSIS:
        await show_ai_analysis(update)
    elif text == BTN_CONSILIUM:
        await consilium_menu(update)
    elif text == CONSILIUM_OPEN:
        await consilium_open_positions(update, context)
    elif text == CONSILIUM_SETUP:
        await consilium_new_setup(update, context)
    elif text == BTN_AI_MARKET:
        await show_market_overview(update)
    elif text == BTN_AI_TRENDS:
        await show_trends(update)
    elif text == BTN_AI_LEARN:
        await show_journal_analysis(update)
    else:
        await update.message.reply_text("Используй кнопки меню 👇", reply_markup=main_menu_keyboard())

# ── Команда /ai_fix ──
async def ai_fix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != CHAT_ID:
        return
    msg = await update.message.reply_text("🤖 Анализирую убыточные сделки...")
    last_trades = db.get_closed_trades(limit=5)
    losing = [t for t in last_trades if t['realized_pnl'] < 0]
    if not losing:
        await msg.edit_text("Убыточных сделок не найдено.")
        return
    trades_text = json.dumps([{'symbol': t['symbol'], 'side': t['side'], 'pnl': t['realized_pnl'], 'comment': t.get('comment', '')} for t in losing], ensure_ascii=False, indent=2)
    prompt = (
        "Трейдер только что закрыл серию убыточных сделок. Проанализируй их и дай рекомендации.\n"
        f"Убыточные сделки:\n{trades_text}\n\n"
        "Определи возможные причины, ошибки в риск-менеджменте или психологии. "
        "Дай конкретные советы, как избежать повторения."
    )
    answer = ai_analyzer.analyze_raw(prompt)
    await msg.edit_text(f"🧠 *AI-разбор убытков:*\n\n{answer[:3500]}")

# ── Ручная синхронизация ──
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != CHAT_ID:
        return
    msg = await update.message.reply_text("🔄 Синхронизирую сделки с BingX...")
    results = await sync_trades(context.bot, update.effective_chat.id)
    new_open = len(results.get('new_open', []))
    new_closed = len(results.get('new_closed', []))
    await msg.edit_text(f"✅ Синхронизация завершена!\n\n🆕 Новых позиций: {new_open}\n🔒 Закрыто позиций: {new_closed}")

# ══════════════════════════════════════════════════════════════════════════════
# НОВЫЕ ФУНКЦИИ КОНСИЛИУМА
# ══════════════════════════════════════════════════════════════════════════════

from ai.consensus_engine import ConsensusEngine
consensus = ConsensusEngine(ai_analyzer.provider)

async def consilium_menu(update: Update):
    keyboard = ReplyKeyboardMarkup([
        [CONSILIUM_OPEN],
        [CONSILIUM_SETUP],
        [BTN_BACK]
    ], resize_keyboard=True)
    await update.message.reply_text("🧠 Консилиум\nВыбери режим:", reply_markup=keyboard)

async def consilium_open_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = get_open_positions()
    if not res.get('success') or not res.get('trades'):
        await update.message.reply_text("Нет открытых позиций или ошибка API.", reply_markup=consilium_keyboard())
        return
    trades = res['trades']
    context.user_data['consilium_positions'] = trades
    keyboard = []
    for t in trades:
        sym = t['symbol']
        side = 'LONG' if t.get('side') == 'BUY' else 'SHORT'
        keyboard.append([f"{sym} {side}"])
    keyboard.append([BTN_BACK])
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Выбери позицию для анализа:", reply_markup=markup)
    context.user_data['state'] = 'consilium_choose_position'

async def consilium_analyze_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    trades = context.user_data.get('consilium_positions', [])
    chosen = None
    for t in trades:
        if f"{t['symbol']} LONG" == text or f"{t['symbol']} SHORT" == text:
            chosen = t
            break
    if not chosen:
        await update.message.reply_text("Выбери позицию из списка.", reply_markup=consilium_keyboard())
        return
    context.user_data['state'] = None
    msg = await update.message.reply_text("🔄 Анализирую позицию...")
    result = await consensus.analyze_open_position(chosen)
    response = (
        f"🧠 Консилиум — {chosen['symbol']} {chosen['side']}\n\n"
        f"📈 Рынок:\n{result['market_review']}\n\n"
        f"⚠️ Риск:\n{result['risk_review']}\n\n"
        f"🧘 Психология:\n{result['psychology_review']}\n\n"
        f"⚖️ Вердикт: {result['judge_verdict']}"
    )
    await msg.edit_text(response)
    await update.message.reply_text("Что дальше?", reply_markup=consilium_keyboard())

async def consilium_new_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 Опишите сетап в свободной форме.\nПримеры:\n• SOL long\n• BTC short\n• Думаю открыть ETH long",
        reply_markup=cancel_keyboard()
    )
    context.user_data['state'] = 'consilium_setup_input'

async def consilium_process_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    from utils.parsers import parse_trade_idea
    ticker, direction = parse_trade_idea(text)
    if not ticker or not direction:
        await update.message.reply_text(
            "Не удалось определить тикер и направление. Укажите явно, например: BTC long",
            reply_markup=cancel_keyboard()
        )
        return
    context.user_data['state'] = None
    msg = await update.message.reply_text("🔄 Анализирую сетап...")
    result = await consensus.analyze_new_setup(ticker, direction, extra_notes=text)
    response = (
        f"🧠 Консилиум — {ticker} {direction}\n\n"
        f"📈 Рынок:\n{result['market_review']}\n\n"
        f"⚠️ Риск:\n{result['risk_review']}\n\n"
        f"🧘 Психология:\n{result['psychology_review']}\n\n"
        f"⚖️ Вердикт: {result['judge_verdict']}"
    )
    await msg.edit_text(response)
    await update.message.reply_text("Что дальше?", reply_markup=consilium_keyboard())

def consilium_keyboard():
    return ReplyKeyboardMarkup([[CONSILIUM_OPEN], [CONSILIUM_SETUP], [BTN_BACK]], resize_keyboard=True)

# ─── Запуск ───────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан! Проверь .env файл.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('sync', sync_command))
    app.add_handler(CommandHandler('ai_fix', ai_fix_command))
    app.add_handler(CommandHandler('health', health_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))
    setup_router(app)
    setup_scheduler(app, db, CHAT_ID)
    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()