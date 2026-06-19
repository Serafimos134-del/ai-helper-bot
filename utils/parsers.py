import re

def parse_trade_idea(text: str):
    text = text.upper().strip()
    ticker_match = re.search(r'\b([A-Z]{2,10})\b', text)
    ticker = ticker_match.group(1) if ticker_match else None
    long_kw = ['LONG', 'ЛОНГ', 'ВВЕРХ', 'РОСТ', 'ПОКУПКА']
    short_kw = ['SHORT', 'ШОРТ', 'ВНИЗ', 'ПАДЕНИЕ', 'ПРОДАЖА']
    if any(w in text for w in long_kw):
        direction = 'LONG'
    elif any(w in text for w in short_kw):
        direction = 'SHORT'
    else:
        direction = None
    return ticker, direction