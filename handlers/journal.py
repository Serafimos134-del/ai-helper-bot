"""
handlers/journal.py
Journal handler — displays closed trades history.
"""

from telegram import Update
from core.container import get_db


async def show_journal(update: Update):
    db = get_db()
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
            exit_p = f"${t['exit_price']:.4f}"
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
                f"   Вход: {entry} | Выход: {exit_p}\n"
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