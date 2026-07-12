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


# Ключевые слова, по которым распознаём "думание вслух" LLM, просочившееся
# в финальный ответ (например: "MONET (нет в списке, ошибся, заменю на
# HYPE)") — модель сама заметила ошибку в процессе генерации, но вместо
# внутреннего рассуждения это попало прямо в пользовательский текст.
_SELF_CORRECTION_MARKERS = (
    'ошиб', 'на самом деле', 'поправлюсь', 'исправля', 'заменю',
    'нет в списке', 'имел в виду', 'опечат', 'перепутал',
)


def strip_llm_self_correction(text: str) -> str:
    """Вырезает скобочные вставки с самокоррекцией LLM (см. докстринг
    _SELF_CORRECTION_MARKERS) — защитный второй рубеж поверх промпта,
    который явно просит модель не включать такие вставки в ответ. Не
    пытается лечить более сложные случаи (самокоррекция не в скобках) —
    это требовало бы структурированного/провалидированного вывода вместо
    свободного текста, что выходит за рамки точечного фикса."""
    def _should_strip(match: 're.Match') -> bool:
        inner = match.group(1).lower()
        return any(marker in inner for marker in _SELF_CORRECTION_MARKERS)

    text = re.sub(r'\(([^()]*)\)', lambda m: '' if _should_strip(m) else m.group(0), text)
    # Убираем возможные двойные пробелы/пробел перед пунктуацией, оставшиеся
    # после вырезания скобок.
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\s+([,.;:])', r'\1', text)
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
