from telegram import ReplyKeyboardMarkup

BTN_CANCEL = "❌ Отмена"

def cancel_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)