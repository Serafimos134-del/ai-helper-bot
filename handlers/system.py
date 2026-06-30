"""
handlers/system.py
System-level handlers: start, help, health, sync, status, ai_fix, test_behavior.
"""

import os
import re
import json
from telegram import Update
from telegram.ext import ContextTypes
from core.container import get_db, get_ai_analyzer
from core.keyboards import main_menu_keyboard
from services.bingx_api import get_balance
from services.auto_sync import sync_trades
from core.scheduler import update_pinned_status

CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')


def _check_chat(update: Update) -> bool:
    return str(update.effective_chat.id) == CHAT_ID


def _clean(text: str) -> str:
    """Убирает markdown LLM чтобы не конфликтовало с Telegram."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__',     r'\1', text)
    text = re.sub(r'`(.+?)`',       r'\1', text)
    return text.strip()


async def _send_long(msg, text: str):
    """Отправляет длинный текст кусками по 4000 символов."""
    if len(text) <= 4000:
        await msg.edit_text(text)
        return
    await msg.edit_text(text[:4000])
    bot = msg.get_bot()
    for i in range(4000, len(text), 4000):
        await bot.send_message(chat_id=msg.chat.id, text=text[i:i+4000])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    db = get_db()

    telegram_id = str(update.effective_chat.id)
    username = update.effective_user.username if update.effective_user else None
    user = db.get_or_create_user(telegram_id, username)
    context.user_data['user_id'] = user['user_id']

    is_owner = _check_chat(update)
    tier_label = "Premium ⭐️" if db.is_premium(user['user_id']) else "Free"

    if is_owner:
        text = (
            "👋 *AI Helper Bot*\n\n"
            "Твой помощник трейдера.\n"
            "Отслеживаю сделки, веду дневник, считаю статистику.\n\n"
            "Используй кнопки меню 👇"
        )
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu_keyboard())
    else:
        text = (
            f"👋 *AI Helper Bot*\n\n"
            f"Твой тариф: {tier_label}\n\n"
            f"Бот сейчас в режиме раннего доступа. "
            f"Полная многопользовательская версия скоро откроется."
        )
        await update.message.reply_text(text, parse_mode='Markdown')


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
        "/status — текущий статус (баланс, позиции, правила)\n"
        "/ai\\_fix — AI-разбор серии убыточных сделок\n"
        "/test\\_behavior — тест детекторов поведения на реальных данных\n"
        "/health — состояние систем"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu_keyboard())


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_chat(update):
        return
    db = get_db()
    ai_analyzer = get_ai_analyzer()
    msg = await update.message.reply_text("🩺 Проверяю здоровье систем...")
    status = []
    try:
        db.get_open_trades()
        status.append("🗄 База данных: 🟢")
    except Exception:
        status.append("🗄 База данных: 🔴")
    try:
        balance = await get_balance()
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


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_chat(update):
        return
    msg = await update.message.reply_text("🔄 Синхронизирую сделки с BingX...")
    results = await sync_trades(context.bot, CHAT_ID)
    new_open   = len(results.get('new_open', []))
    new_closed = len(results.get('new_closed', []))
    await msg.edit_text(
        f"✅ Синхронизация завершена!\n\n"
        f"🆕 Новых позиций: {new_open}\n"
        f"🔒 Закрыто позиций: {new_closed}"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_chat(update):
        return
    db = get_db()
    await update_pinned_status(context, db, CHAT_ID, force=True)
    await update.message.reply_text(
        "📌 Статус обновлён. Смотри закреплённое сообщение.",
        reply_markup=main_menu_keyboard()
    )


async def ai_fix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_chat(update):
        return
    db = get_db()
    ai_analyzer = get_ai_analyzer()
    msg = await update.message.reply_text("🤖 Анализирую убыточные сделки...")
    last_trades = db.get_closed_trades(limit=5)
    losing = [t for t in last_trades if t['realized_pnl'] < 0]
    if not losing:
        await msg.edit_text("Убыточных сделок не найдено.")
        return
    trades_text = json.dumps(
        [{'symbol': t['symbol'], 'side': t['side'], 'pnl': t['realized_pnl'],
          'comment': t.get('comment', '')} for t in losing],
        ensure_ascii=False, indent=2
    )
    prompt = (
        "Проанализируй убыточные сделки трейдера. Отвечай строго по структуре:\n\n"
        "ОШИБКИ (максимум 3, каждая одним предложением):\n"
        "1. ...\n"
        "2. ...\n"
        "3. ...\n\n"
        "ГЛАВНАЯ ПРИЧИНА (одно предложение):\n"
        "...\n\n"
        "ЧТО СДЕЛАТЬ ПРЯМО СЕЙЧАС (максимум 2 пункта):\n"
        "1. ...\n"
        "2. ...\n\n"
        "Без вступления, без воды, только факты из данных ниже.\n"
        f"Сделки:\n{trades_text}"
    )
    answer = _clean(await ai_analyzer.analyze_raw(prompt, max_tokens=1500))
    await _send_long(msg, f"🧠 AI-разбор убытков:\n\n{answer}")


async def test_behavior_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_chat(update):
        return
    from services.database import Database
    from services.behavior_engine import BehaviorEngine, format_alert
    from services.bingx_api import get_kline

    db = Database()
    engine = BehaviorEngine(db)
    user_id = 'default'

    msg = await update.message.reply_text("🧪 Тестирую детекторы поведения на реальных данных...")
    results = []

    overtrading = engine.detect_overtrading(user_id)
    if overtrading:
        results.append(("Overtrading", format_alert(overtrading)))
    else:
        results.append(("Overtrading", "Не сработал — частота входов в норме"))

    closed = db.get_closed_trades(limit=10, user_id=user_id)
    panic_hits = []
    for t in closed:
        panic = engine.detect_panic_close(t)
        if panic:
            panic_hits.append(f"{t['symbol']} (PNL ${float(t['realized_pnl']):.2f})")
    if panic_hits:
        results.append(("Panic Close", f"Сработал бы на: {', '.join(panic_hits)}"))
    else:
        results.append(("Panic Close", "Не сработал — нет быстрых закрытий в убыток без стопа"))

    open_trades = db.get_open_trades(user_id=user_id)
    if open_trades:
        candidate = open_trades[0]
        fake_new_trade = {
            'symbol': candidate['symbol'],
            'entryPrice': candidate['entry_price'],
            'positionAmt': candidate['quantity'],
            'side': candidate['side']
        }
        revenge = engine.detect_revenge_trading(user_id, fake_new_trade)
        if revenge:
            results.append(("Revenge Trading", format_alert(revenge)))
        else:
            results.append(("Revenge Trading", f"Не сработал на {candidate['symbol']} — нет признаков отыгрыша"))

        kline_result = await get_kline(candidate['symbol'], "1h", 2)
        if kline_result.get('success'):
            fomo = engine.detect_fomo(fake_new_trade, kline_result.get('klines', []))
            if fomo:
                results.append(("FOMO", format_alert(fomo)))
            else:
                results.append(("FOMO", f"Не сработал на {candidate['symbol']} — вход не выглядит погоней"))
        else:
            results.append(("FOMO", "Не удалось получить данные свечей"))
    else:
        results.append(("Revenge Trading", "Нет открытых позиций для проверки"))
        results.append(("FOMO", "Нет открытых позиций для проверки"))

    text = "🧪 Результаты теста Behavior Engine:\n\n"
    for name, result in results:
        text += f"▪️ {name}:\n{result}\n\n"

    await msg.edit_text(text[:4096])
