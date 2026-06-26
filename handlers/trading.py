"""
handlers/trading.py
Trading-related handlers: balance, last trades, stats, AI analysis.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from core.container import get_db, get_ai_analyzer
from core.keyboards import trading_menu_keyboard
from services.bingx_api import get_balance
from services.trading_stats import format_stats_message


async def show_balance(update: Update):
    db = get_db()
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
    else:
        text = f"❌ Ошибка получения баланса:\n`{result.get('error', 'Неизвестная ошибка')}`"
    await msg.edit_text(text, parse_mode='Markdown')


async def show_last_trades(update: Update):
    db = get_db()
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
    db = get_db()
    msg = await update.message.reply_text("⏳ Считаю статистику...")
    stats = db.get_stats()
    text = format_stats_message(stats)
    await msg.edit_text(text, parse_mode='Markdown')


async def show_ai_analysis(update: Update):
    ai_analyzer = get_ai_analyzer()
    msg = await update.message.reply_text("🤖 Анализирую...")
    text = ai_analyzer.analyze()
    await msg.edit_text(text, parse_mode='Markdown')