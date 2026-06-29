"""
handlers/trading.py
Trading-related handlers: balance, last trades, stats, AI analysis.
"""

import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from core.container import get_db, get_ai_analyzer
from core.keyboards import trading_menu_keyboard
from services.bingx_api import get_balance
from services.trading_stats import format_stats_message


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
    # Первый кусок через edit, остальные через reply
    await msg.edit_text(text[:4000])
    chat_id = msg.chat.id
    bot = msg.get_bot()
    for i in range(4000, len(text), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000])


async def show_balance(update: Update):
    msg = await update.message.reply_text("⏳ Получаю баланс...")
    result = await get_balance()
    if result.get('success'):
        text = (
            f"💰 *Баланс аккаунта*\n\n"
            f"📊 Эквити: ${result['equity']:.2f} USDT\n"
            f"✅ Доступно: ${result['available']:.2f} USDT\n"
            f"🔒 Использовано: ${result['used_margin']:.2f} USDT\n"
            f"📈 Нереализованный PNL: ${result['unrealized_pnl']:+.2f} USDT"
        )
        await msg.edit_text(text, parse_mode='Markdown')
    else:
        await msg.edit_text(f"❌ Ошибка получения баланса: {result.get('error', 'Неизвестная ошибка')}")


async def show_last_trades(update: Update):
    db = get_db()
    msg = await update.message.reply_text("⏳ Загружаю сделки...")
    open_trades   = db.get_open_trades()
    closed_trades = db.get_closed_trades(limit=15)
    lines = ["📋 *Последние сделки*\n"]

    if open_trades:
        lines.append("🔓 *Открытые позиции:*")
        for t in open_trades:
            pnl = float(t.get('unrealized_pnl', 0))
            emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
            lines.append(
                f"{emoji} {t.get('symbol')} {t.get('side')} "
                f"| Вход: ${float(t.get('entry_price', 0)):.4f} "
                f"| PNL: ${pnl:+.2f}"
            )
    else:
        lines.append("🔓 Открытых позиций нет")

    keyboard = []
    if closed_trades:
        lines.append("\n✅ *Последние закрытые (нажми для деталей):*")
        for t in reversed(closed_trades):
            pnl   = float(t.get('realized_pnl', 0))
            emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
            label = f"{emoji} {t['symbol']} {pnl:+.2f}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"detail_{t['id']}")])
    else:
        lines.append("\n✅ Закрытых сделок нет")

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await msg.edit_text("\n".join(lines), parse_mode='Markdown', reply_markup=reply_markup)


async def show_stats(update: Update):
    db = get_db()
    msg  = await update.message.reply_text("⏳ Считаю статистику...")
    stats = db.get_stats()
    text  = format_stats_message(stats)
    await msg.edit_text(text, parse_mode='Markdown')


async def show_ai_analysis(update: Update):
    ai_analyzer = get_ai_analyzer()
    msg  = await update.message.reply_text("🤖 Анализирую...")
    text = _clean(ai_analyzer.analyze())
    await _send_long(msg, text)
