"""
handlers/system.py
System-level handlers: start, help, health, sync, status, ai_fix.
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
    if not _check_chat(update):
        return
    context.user_data.clear()
    text = (
        "👋 *AI Helper Bot*\n\n"
        "Твой помощник трейдера.\n"
        "Отслеживаю сделки, веду дневник, считаю статистику.\n\n"
        "Используй кнопки меню 👇"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu_keyboard())


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

    answer = _clean(await ai_analyzer.analyze_raw(prompt))
    await _send_long(msg, f"🧠 AI-разбор убытков:\n\n{answer}")
