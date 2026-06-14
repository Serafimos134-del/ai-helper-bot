import logging
import os
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from services.bingx_api import get_balance, get_open_positions, get_closed_orders
from services.trading_storage import (
    get_open_trades,
    get_closed_trades,
)
from services.trading_stats import calculate_stats, format_stats_message
from services.comment_manager import get_trades_for_comment, save_comment
from services.auto_sync import sync_trades
from services.ai_trading import AITradingAnalyzer

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

ai_analyzer = AITradingAnalyzer()

# ─── Тексты кнопок ──────────────────────────────────────────────────────────

BTN_TRADING = "📈 Trading"
BTN_AI = "🤖 AI"
BTN_SHORTS = "🎬 Shorts"
BTN_HELP = "ℹ️ Help"

BTN_BALANCE = "💰 Баланс"
BTN_LAST_TRADES = "📋 Последние сделки"
BTN_STATS = "📊 Статистика"
BTN_COMMENT = "✏️ Комментарий"
BTN_AI_ANALYSIS = "🧠 AI-анализ"

BTN_BACK = "🔙 Назад"
BTN_CANCEL = "❌ Отмена"

# ─── Клавиатуры (Reply, снизу) ────────────────────────────────────────────────

def main
