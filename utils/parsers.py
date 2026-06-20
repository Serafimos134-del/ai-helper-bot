import re

# Зарезервированные слова, которые не должны считаться тикерами
_RESERVED = {'LONG', 'SHORT', 'ЛОНГ', 'ШОРТ', 'ВВЕРХ', 'ВНИЗ', 'РОСТ', 'ПАДЕНИЕ', 'ПОКУПКА', 'ПРОДАЖА', 'BUY', 'SELL'}

def parse_trade_idea(text: str):
    text = text.upper().strip()
    # Ищем все кандидаты
    words = re.findall(r'\b[A-Z]{2,10}\b', text)
    ticker = None
    for w in words:
        if w not in _RESERVED:
            ticker = w
            break
    long_kw = ['LONG', 'ЛОНГ', 'ВВЕРХ', 'РОСТ', 'ПОКУПКА', 'BUY']
    short_kw = ['SHORT', 'ШОРТ', 'ВНИЗ', 'ПАДЕНИЕ', 'ПРОДАЖА', 'SELL']
    if any(w in text for w in long_kw):
        direction = 'LONG'
    elif any(w in text for w in short_kw):
        direction = 'SHORT'
    else:
        direction = None
    return ticker, direction