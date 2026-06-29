"""
handlers/journal.py
Journal handler — displays closed trades history.
"""

from telegram import Update
from core.container import get_db


def _format_duration(holding_minutes) -> str:
    if holding_minutes is None:
        return "—"
    h, m = divmod(int(holding_minutes), 60)
    if h:
        return f"{h}ч {m}мин"
    return f"{m} мин"


def _escape(text: str) -> str:
    """Экранирует пользовательский текст для безопасной отправки."""
    if not text or text == '—':
        return text
    # Убираем символы которые ломают Telegram Markdown
    for ch in ['*', '_', '`', '[', ']']:
        text = text.replace(ch, '')
    return text


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
            symbol   = t['symbol']
            side     = t['side']
            entry    = f"${t['entry_price']:.4f}"
            exit_p   = f"${t['exit_price']:.4f}"
            pnl      = float(t['realized_pnl'])
            emoji    = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
            volume   = t['quantity']
            leverage = t.get('leverage', 1)
            stop     = f"${t['stop_loss']:.4f}"  if t.get('stop_loss')  else "—"
            take     = f"${t['take_profit']:.4f}" if t.get('take_profit') else "—"
            open_time  = t.get('open_time')  or "—"
            close_time = t.get('close_time') or t.get('closed_at') or "—"
            entry_comment = _escape(t.get('entry_comment') or '—')
            exit_comment  = _escape(t.get('exit_comment') or t.get('comment') or '—')
            ai_review     = t.get('ai_review', '')
            duration      = _format_duration(t.get('holding_minutes'))
            market_trend  = t.get('market_trend') or '—'
            setup         = t.get('setup_type') or '—'

            line = (
                f"{emoji} *{symbol}* {side}\n"
                f"   Вход: {entry} | Выход: {exit_p}\n"
                f"   Объём: {volume} | Плечо: {leverage}x\n"
                f"   Стоп: {stop} | Тейк: {take}\n"
                f"   PNL: ${pnl:.2f}\n"
                f"   Длительность: {duration}\n"
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
        # Режем по 4000 и отправляем частями
        for i in range(0, max(len(text), 1), 4000):
            await update.message.reply_text(
                text[i:i+4000],
                parse_mode='Markdown'
            )
