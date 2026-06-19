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
BTN_AI_EVALUATION = "🤖 Оценка сделки"
BTN_AI_ANALYSIS = "🧠 AI-анализ"

BTN_BACK = "🔙 Назад"
BTN_CANCEL = "❌ Отмена"

# AI-меню
BTN_AI_OPEN_ANALYSIS = "📈 Анализ открытых сделок"
BTN_AI_ASK = "💬 Задать вопрос AI"
BTN_AI_MARKET = "🌐 Обзор рынка"
BTN_AI_TRENDS = "📊 Тренды"
BTN_AI_LEARN = "📊 Анализ журнала"

NAV_BUTTONS = {
    BTN_TRADING, BTN_AI, BTN_JOURNAL, BTN_HELP,
    BTN_BALANCE, BTN_LAST_TRADES, BTN_STATS, BTN_AI_EVALUATION, BTN_AI_ANALYSIS,
    BTN_BACK, BTN_CANCEL,
    BTN_AI_OPEN_ANALYSIS, BTN_AI_ASK, BTN_AI_MARKET, BTN_AI_TRENDS, BTN_AI_LEARN,
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
            [BTN_STATS, BTN_AI_EVALUATION],
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

async def show_market_overview(update: Update):
    msg = await update.message.reply_text("🌐 Собираю данные рынка...")

    result = get_top_tickers(10)
    if not result.get('success') or not result.get('tickers'):
        await msg.delete()
        await update.message.reply_text(
            f"❌ Не удалось получить данные рынка: {result.get('error', 'нет данных')}",
            reply_markup=ai_menu_keyboard()
        )
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

    try:
        await msg.delete()
    except Exception:
        pass

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
                    data_lines.append(
                        f"{sym}: изменение за 24ч {change:+.2f}%, "
                        f"максимум {max(highs):.2f}, минимум {min(lows):.2f}, "
                        f"текущая цена {last_close:.2f}"
                    )
            except (ValueError, TypeError, AttributeError):
                continue

    if not data_lines:
        try:
            await msg.delete()
        except Exception:
            pass
        await update.message.reply_text(
            "❌ Не удалось получить данные по трендам.",
            reply_markup=ai_menu_keyboard()
        )
        return

    prompt = (
        "Тренд-анализ на основе часовых свечей за 24 часа:\n"
        + "\n".join(data_lines)
        + "\n\nТвой ответ дай строго в формате:\n"
        + "1. BTC: тренд (восходящий/нисходящий/боковик), ключевые уровни поддержки и сопротивления на сегодня\n"
        + "2. ETH: аналогично\n"
        + "3. СИГНАЛ: если видишь явную точку входа по любой из монет — укажи направление, цену входа и стоп-лосс. "
        + "Если явного сигнала нет — напиши «явного сигнала нет»\n\n"
        + "Кратко, без воды, используй цифры из данных выше."
    )

    try:
        analysis = ai_analyzer.analyze_raw(prompt)
    except Exception as e:
        analysis = f"Ошибка AI: {e}"

    try:
        await msg.delete()
    except Exception:
        pass

    await update.message.reply_text(f"📊 Тренды от AI\n\n{analysis[:3500]}", reply_markup=ai_menu_keyboard())

async def start_open_position_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = get_open_positions().get('trades', [])
    if not positions:
        await update.message.reply_text(
            "✅ Нет открытых позиций для анализа.",
            reply_markup=ai_menu_keyboard()
        )
        return

    positions_map = {}
    for p in positions:
        label = f"{p.get('symbol', '?')} {p.get('side', '')}"
        positions_map[label] = p

    context.user_data['open_positions_map'] = positions_map
    context.user_data['state'] = 'choosing_position'

    await update.message.reply_text(
        "📈 *Выбери позицию для AI-анализа:*",
        parse_mode='Markdown',
        reply_markup=open_positions_keyboard(positions)
    )

async def analyze_open_position(update: Update, position: dict):
    msg = await update.message.reply_text("🤖 Анализирую позицию...")

    symbol = position.get('symbol', '')
    side = position.get('side', '')
    entry_price = float(position.get('entryPrice', 0))
    unrealized_pnl = float(position.get('unrealizedPnl', 0))
    size = position.get('size', '')

    support = None
    resistance = None
    current_price = entry_price

    kline_result = get_kline(symbol, "15m", 50)
    klines = kline_result.get('klines', [])
    if kline_result.get('success') and len(klines) >= 10:
        try:
            highs = [float(k.get('high', k.get('h', 0))) for k in klines[-10:]]
            lows = [float(k.get('low', k.get('l', 0))) for k in klines[-10:]]
            closes = [float(k.get('close', k.get('c', 0))) for k in klines]
            resistance = max(highs)
            support = min(lows)
            current_price = closes[-1]
        except (ValueError, TypeError, AttributeError):
            pass

    change_pct = 0
    if entry_price:
        if side == 'LONG':
            change_pct = (current_price - entry_price) / entry_price * 100
        else:
            change_pct = (entry_price - current_price) / entry_price * 100

    prompt = (
        "Ты — профессиональный риск-менеджер. Проанализируй открытую позицию строго по пунктам, "
        "без общих фраз, только конкретные рекомендации.\n\n"
        "ДАННЫЕ ПОЗИЦИИ:\n"
        f"- Символ: {symbol}\n"
        f"- Направление: {side}\n"
        f"- Цена входа: {entry_price}\n"
        f"- Текущая цена: {current_price}\n"
        f"- Изменение от входа: {change_pct:+.2f}%\n"
        f"- Нереализованный PNL: {unrealized_pnl:+.2f} USDT\n"
        f"- Объём позиции: {size}\n"
        + (f"- Ближайшее сопротивление: {resistance}\n" if resistance else "")
        + (f"- Ближайшая поддержка: {support}\n" if support else "")
        + "\nОТВЕТ ДАЙ СТРОГО В ФОРМАТЕ:\n"
        "1. РЕКОМЕНДАЦИЯ: (удерживать / частично закрыть / закрыть полностью)\n"
        "2. ГДЕ ПОСТАВИТЬ СТОП-ЛОСС: (конкретная цена)\n"
        "3. ГДЕ ЗАФИКСИРОВАТЬ ПРИБЫЛЬ: (конкретная цена)\n"
        "4. ОБОСНОВАНИЕ: (2-3 предложения, с указанием уровней)"
    )

    try:
        analysis = ai_analyzer.analyze_raw(prompt)
    except Exception as e:
        analysis = f"Ошибка AI: {e}"

    try:
        await msg.delete()
    except Exception:
        pass

    await update.message.reply_text(
        f"📈 Анализ позиции {symbol} {side}\n\n{analysis[:3500]}",
        reply_markup=ai_menu_keyboard()
    )

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
            if pnl > 0:
                emoji = "✅"
            elif pnl < 0:
                emoji = "❌"
            else:
                emoji = "➖"
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
    await msg.edit_text(f"📊 *Анализ журнала:*\n\n{answer[:3500]}", parse_mode='Markdown')


# ── Выбор сделки для AI-оценки ──
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

async def show_help(update: Update):
    text = (
        "ℹ️ *Помощь*\n\n"
        "📈 *Trading* — работа со сделками:\n"
        "  • 💰 Баланс — текущий баланс BingX\n"
        "  • 📋 Последние сделки — открытые и закрытые позиции\n"
        "  • 📊 Статистика — Win Rate, PNL и др.\n"
        "  • 🤖 Оценка сделки — AI-оценка выбранной сделки\n"
        "  • 🧠 AI-анализ — анализ торговли\n\n"
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
    msg = await update.message.reply_text("🩺 Проверяю здоровье систем...")
    status = []

    # 1. Database
    try:
        db.get_open_trades()
        status.append("🗄 База данных: 🟢")
    except Exception:
        status.append("🗄 База данных: 🔴")

    # 2. BingX API
    try:
        balance = get_balance()
        if balance.get('success'):
            status.append("📡 BingX API: 🟢")
        else:
            status.append(f"📡 BingX API: 🔴 ({balance.get('error', 'неизвестно')})")
    except Exception as e:
        status.append(f"📡 BingX API: 🔴 ({e})")

    # 3. AI-провайдер (реальная проверка)
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

    # 4. Прокси
    if os.getenv('HTTP_PROXY') or os.getenv('HTTPS_PROXY'):
        status.append("🌐 Прокси: 🟢 (настроен)")
    else:
        status.append("🌐 Прокси: ⚪ (не используется)")

    await msg.edit_text(
        "📊 *Health Check*\n\n" + "\n".join(status),
        parse_mode='Markdown'
    )

# ── Главный обработчик сообщений ──
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    state = context.user_data.get('state')

    if state in ('entering_comment_inline', 'entering_exit_reason', 'entering_entry_reason'):
        if text in NAV_BUTTONS or text.startswith("🏠 *"):
            await update.message.reply_text(
                "⚠️ Это навигационная кнопка, а не комментарий. Пожалуйста, введите текст комментария.",
                reply_markup=cancel_keyboard()
            )
            return

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

    if state == 'entering_exit_reason':
        if text == BTN_CANCEL:
            context.user_data['state'] = None
            context.user_data.pop('comment_order_id', None)
            await update.message.reply_text("Отменено.", reply_markup=trading_menu_keyboard())
            return
        trade_id = context.user_data.get('comment_order_id')
        if trade_id:
            db.update_trade_metrics(trade_id, exit_comment=text)
            await update.message.reply_text("✅ Вывод сохранён.", reply_markup=trading_menu_keyboard())
        context.user_data['state'] = None
        context.user_data.pop('comment_order_id', None)
        return

    if state == 'entering_entry_reason':
        if text == BTN_CANCEL:
            context.user_data['state'] = None
            await update.message.reply_text("Отменено.", reply_markup=trading_menu_keyboard())
            return
        order_id = context.user_data.get('entry_order_id')
        if order_id:
            db.update_open_trade_by_order_id(order_id, entry_comment=text)
            await update.message.reply_text("✅ Причина входа сохранена.", reply_markup=trading_menu_keyboard())
        context.user_data['state'] = None
        return

    if state == 'asking_ai':
        if text == BTN_CANCEL:
            context.user_data['state'] = None
            await update.message.reply_text("Отменено.", reply_markup=ai_menu_keyboard())
            return

        context.user_data['state'] = None
        msg = await update.message.reply_text("🤖 Думаю...")

        ticker_match = re.search(r'\b([A-Z0-9]{2,}-USDT)\b', text.upper())
        symbol = ticker_match.group(1) if ticker_match else None

        if symbol:
            ticker_data = get_ticker(symbol)
            kline_data = get_kline(symbol, "1h", 24)

            extra_context = ""
            if ticker_data.get('success'):
                t = ticker_data['ticker']
                extra_context += (
                    f"Текущая цена {symbol}: {t.get('lastPrice', 'N/A')} USDT, "
                    f"изменение за 24ч: {t.get('priceChangePercent', 'N/A')}%, "
                    f"макс: {t.get('highPrice', 'N/A')}, мин: {t.get('lowPrice', 'N/A')}, "
                    f"объём: {t.get('quoteVolume', 'N/A')}.\n"
                )
            else:
                extra_context += f"Не удалось получить данные по {symbol}.\n"

            if kline_data.get('success') and kline_data.get('klines'):
                klines = kline_data['klines']
                closes = [float(k[4]) for k in klines]
                if closes[0] != 0:
                    change_24h = ((closes[-1] - closes[0]) / closes[0]) * 100
                    high_24h = max(float(k[2]) for k in klines)
                    low_24h = min(float(k[3]) for k in klines)
                    extra_context += (
                        f"За последние 24 часа: изменение {change_24h:+.2f}%, "
                        f"максимум {high_24h}, минимум {low_24h}."
                    )
            else:
                extra_context += "Не удалось получить свечные данные."

            prompt = (
                f"Ты — профессиональный трейдер-ментор. Проанализируй монету {symbol} "
                f"на основе предоставленных данных и вопроса пользователя.\n\n"
                f"{extra_context}\n\n"
                f"Вопрос: {text}\n\n"
                f"Дай конкретный, структурированный ответ: тренд, ключевые уровни, рекомендация (входить/не входить), "
                f"стоп-лосс и тейк-профит (если применимо). Будь краток."
            )
        else:
            market_context = ""
            try:
                tickers_result = get_top_tickers(5)
                if tickers_result.get('success') and tickers_result.get('tickers'):
                    lines = []
                    for t in tickers_result['tickers']:
                        s = t.get('symbol', '')
                        price = t.get('lastPrice', t.get('close', ''))
                        change = float(t.get('priceChangePercent', 0))
                        lines.append(f"{s}: цена {price}, изм за 24ч {change:+.2f}%")
                    market_context = "Актуальные данные рынка (топ-5 по объёму):\n" + "\n".join(lines) + "\n\n"
            except Exception:
                market_context = ""

            prompt = (
                market_context
                + f"ВОПРОС ТРЕЙДЕРА: {text}\n\n"
                + "Если вопрос касается цены или текущей рыночной ситуации — используй ТОЛЬКО данные выше. "
                + "Если нужной монеты нет в данных или вопрос не про рынок — отвечай по своим знаниям, "
                + "но никогда не придумывай конкретные цифры цен, которых не видел. "
                + "В таком случае честно скажи, что не можешь дать точную цифру, и предложи проверить на бирже."
            )

        try:
            answer = ai_analyzer.analyze_raw(prompt)
        except Exception as e:
            answer = f"Ошибка вызова AI: {e}"

        try:
            await msg.delete()
        except Exception:
            pass

        await update.message.reply_text(f"💬 Ответ AI:\n\n{answer[:3500]}", reply_markup=ai_menu_keyboard())
        return

    if state == 'choosing_position':
        if text == BTN_BACK:
            context.user_data['state'] = None
            context.user_data.pop('open_positions_map', None)
            await update.message.reply_text(
                "🤖 *AI-Ассистент*\nВыбери, что хочешь проанализировать:",
                parse_mode='Markdown',
                reply_markup=ai_menu_keyboard()
            )
            return

        positions_map = context.user_data.get('open_positions_map', {})
        position = positions_map.get(text)

        if not position:
            await update.message.reply_text(
                "Выбери позицию из списка кнопок 👇",
                reply_markup=open_positions_keyboard(list(positions_map.values()))
            )
            return

        context.user_data['state'] = None
        context.user_data.pop('open_positions_map', None)
        await analyze_open_position(update, position)
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
    elif text == BTN_AI_EVALUATION:
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

# ── Команда /ai_fix ──
async def ai_fix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🤖 Анализирую убыточные сделки...")
    last_trades = db.get_closed_trades(limit=5)
    losing = [t for t in last_trades if t['realized_pnl'] < 0]
    if not losing:
        await msg.edit_text("Убыточных сделок не найдено.")
        return

    trades_text = json.dumps([{
        'symbol': t['symbol'],
        'side': t['side'],
        'pnl': t['realized_pnl'],
        'comment': t.get('comment', '')
    } for t in losing], ensure_ascii=False, indent=2)

    prompt = (
        "Трейдер только что закрыл серию убыточных сделок. Проанализируй их и дай рекомендации.\n"
        f"Убыточные сделки:\n{trades_text}\n\n"
        "Определи возможные причины, ошибки в риск-менеджменте или психологии. "
        "Дай конкретные советы, как избежать повторения."
    )
    answer = ai_analyzer.analyze_raw(prompt)
    await msg.edit_text(f"🧠 *AI-разбор убытков:*\n\n{answer[:3500]}", parse_mode='Markdown')

# ── Ручная синхронизация ──
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 Синхронизирую сделки с BingX...")
    results = await sync_trades(context.bot, update.effective_chat.id)
    new_open = len(results.get('new_open', []))
    new_closed = len(results.get('new_closed', []))
    await msg.edit_text(f"✅ Синхронизация завершена!\n\n🆕 Новых позиций: {new_open}\n🔒 Закрыто позиций: {new_closed}")

# ─── Запуск ───────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан! Проверь .env файл.")

    # Инициализация базы данных (создание таблиц и миграции)
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