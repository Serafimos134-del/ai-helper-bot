"""
core/keyboards.py
Centralized keyboard definitions for the bot.
"""

from telegram import ReplyKeyboardMarkup

# Button texts
BTN_TRADING    = "📈 Trading"
BTN_AI         = "🤖 AI"
BTN_JOURNAL    = "📓 Журнал"
BTN_HELP       = "ℹ️ Help"

BTN_BALANCE     = "💰 Баланс"
BTN_LAST_TRADES = "📋 Последние сделки"
BTN_STATS       = "📊 Статистика"
BTN_AI_ANALYSIS = "🧠 AI-анализ"

BTN_BACK   = "🔙 Назад"
BTN_CANCEL = "❌ Отмена"

BTN_CONSILIUM  = "🧠 Консилиум"
CONSILIUM_OPEN  = "📂 Открытые сделки"
CONSILIUM_SETUP = "🎯 Новый сетап"

BTN_AI_MARKET = "🌐 Обзор рынка"
BTN_AI_TRENDS = "📊 Тренды"
BTN_AI_LEARN  = "📊 Анализ журнала"
BTN_AI_COACH  = "🎯 AI Coach"
BTN_TRADER_DNA = "🧬 Trader DNA"

# Navigation buttons set
NAV_BUTTONS = {
    BTN_TRADING, BTN_AI, BTN_JOURNAL, BTN_HELP,
    BTN_BALANCE, BTN_LAST_TRADES, BTN_STATS, BTN_AI_ANALYSIS,
    BTN_BACK, BTN_CANCEL,
    BTN_AI_MARKET, BTN_AI_TRENDS, BTN_AI_LEARN, BTN_AI_COACH, BTN_TRADER_DNA,
    BTN_CONSILIUM, CONSILIUM_OPEN, CONSILIUM_SETUP,
}


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[BTN_TRADING], [BTN_AI, BTN_JOURNAL], [BTN_HELP]],
        resize_keyboard=True
    )


def trading_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[BTN_BALANCE, BTN_LAST_TRADES], [BTN_STATS, BTN_AI_ANALYSIS], [BTN_BACK]],
        resize_keyboard=True
    )


def ai_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[BTN_CONSILIUM], [BTN_AI_MARKET, BTN_AI_TRENDS], [BTN_AI_LEARN, BTN_AI_COACH],
         [BTN_TRADER_DNA], [BTN_BACK]],
        resize_keyboard=True
    )


def cancel_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)


# Выбор биржи при /setkeys (задача от 13.07.2026 — "мультибиржевость
# обязательна"). Порядок — BingX первым, единственная биржа, проверенная
# на реальном трафике (см. services/bingx_api.py); остальные три сверены
# только с документацией. OKX сознательно не в списке — не реализован
# (services/exchanges/registry.py).
EXCHANGE_LABELS = {
    'BingX':   'bingx',
    'Bybit':   'bybit',
    'Binance': 'binance',
    'MEXC':    'mexc',
}


def exchange_choice_keyboard():
    return ReplyKeyboardMarkup(
        [['BingX'], ['Bybit', 'Binance'], ['MEXC'], [BTN_CANCEL]],
        resize_keyboard=True
    )
