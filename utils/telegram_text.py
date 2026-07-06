"""
utils/telegram_text.py
Общие хелперы форматирования/отправки текста в Telegram-хендлерах.
Раньше _clean() и _send_long() были продублированы дословно в handlers/trading.py
и handlers/system.py (и похожий _send_chunks в handlers/ai.py) — вынесены сюда.
"""

import re


def clean_markdown(text: str) -> str:
    """Убирает markdown LLM, чтобы не конфликтовало с Telegram."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__',     r'\1', text)
    text = re.sub(r'`(.+?)`',       r'\1', text)
    return text.strip()


async def send_long(msg, text: str, limit: int = 4000):
    """Отправляет длинный текст кусками (редактирует исходное сообщение,
    остальные куски — новыми сообщениями)."""
    if len(text) <= limit:
        await msg.edit_text(text)
        return
    await msg.edit_text(text[:limit])
    bot = msg.get_bot()
    for i in range(limit, len(text), limit):
        await bot.send_message(chat_id=msg.chat.id, text=text[i:i + limit])
