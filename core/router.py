import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
)
from services.database import Database
from services.ai_trading import AITradingAnalyzer
from core.keyboards import cancel_keyboard

logger = logging.getLogger(__name__)

db = Database()
ai_analyzer = AITradingAnalyzer()


def setup_router(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(handle_callback))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("comment_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        trade_id = int(parts[1])
        context.user_data['comment_order_id'] = trade_id
        context.user_data['state'] = 'entering_comment_inline'
        await query.edit_message_text(
            f"✏️ *Напишите комментарий* к сделке #{trade_id}:",
            parse_mode='Markdown',
            reply_markup=cancel_keyboard()
        )
    elif data.startswith("detail_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        trade_id = int(parts[1])
        trade = db.find_trade_by_id(trade_id)
        if trade:
            holding = trade.get('holding_minutes')
            duration_str = f"{holding} мин" if holding is not None else "—"
            detail_text = (
                f"📊 *Детали сделки #{trade_id}*\n\n"
                f"Символ: {trade['symbol']}\n"
                f"Сторона: {trade['side']}\n"
                f"Вход: ${trade['entry_price']:.4f}\n"
                f"Выход: ${trade['exit_price']:.4f}\n"
                f"Объём: {trade['quantity']}\n"
                f"Плечо: {trade.get('leverage', 1)}x\n"
                f"Длительность: {duration_str}\n"
                f"PNL: ${trade['realized_pnl']:.2f}\n"
                f"Тренд рынка: {trade.get('market_trend', '—')}\n"
                f"Сетап: {trade.get('setup_type', '—')}\n"
                f"Комментарий: {trade.get('exit_comment') or trade.get('comment', '—')}\n"
                f"Закрыта: {trade.get('close_time') or trade.get('closed_at', '—')}"
            )
            detail_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Добавить комментарий", callback_data=f"comment_{trade_id}")]
            ])
            await query.edit_message_text(detail_text, parse_mode='Markdown', reply_markup=detail_keyboard)
        else:
            await query.edit_message_text("❌ Сделка не найдена.")
    elif data.startswith("eval_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        trade_id = int(parts[1])
        await generate_ai_review(query, trade_id)
    elif data.startswith("entry_reason_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            await query.edit_message_text("❌ Ошибка: неверный ID позиции.")
            return
        order_id = parts[2]
        context.user_data['entry_order_id'] = order_id
        context.user_data['state'] = 'entering_entry_reason'
        await query.edit_message_text(
            "✏️ Напишите причину входа:",
            reply_markup=cancel_keyboard()
        )
    elif data == "skip_entry_reason":
        await query.edit_message_text("Причина входа пропущена.")
    elif data.startswith("exit_reason_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        trade_id = int(parts[2])
        context.user_data['comment_order_id'] = trade_id
        context.user_data['state'] = 'entering_exit_reason'
        await query.edit_message_text(
            "✏️ Напишите вывод по сделке (что поняли):",
            reply_markup=cancel_keyboard()
        )
    elif data.startswith("ai_review_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        trade_id = int(parts[2])
        await generate_ai_review(query, trade_id)
    elif data == "skip_comment":
        await query.edit_message_text("Запись сохранена без комментария.")
    elif data.startswith("setup_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        trade_id = int(parts[1])
        context.user_data['setup_trade_id'] = trade_id
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Liquidity Sweep", callback_data=f"set_setup_{trade_id}_LiquiditySweep")],
            [InlineKeyboardButton("FVG", callback_data=f"set_setup_{trade_id}_FVG")],
            [InlineKeyboardButton("BOS", callback_data=f"set_setup_{trade_id}_BOS")],
            [InlineKeyboardButton("CHOCH", callback_data=f"set_setup_{trade_id}_CHOCH")],
            [InlineKeyboardButton("Retest", callback_data=f"set_setup_{trade_id}_Retest")],
            [InlineKeyboardButton("Breakout", callback_data=f"set_setup_{trade_id}_Breakout")],
            [InlineKeyboardButton("Scalp", callback_data=f"set_setup_{trade_id}_Scalp")],
            [InlineKeyboardButton("Other", callback_data=f"set_setup_{trade_id}_Other")],
            [InlineKeyboardButton("🔙 Отмена", callback_data="cancel_setup")]
        ])
        await query.edit_message_text("📊 *Выберите сетап сделки:*", parse_mode='Markdown', reply_markup=keyboard)
    elif data.startswith("set_setup_"):
        parts = data.split("_", 3)
        if len(parts) < 4:
            return
        trade_id = int(parts[2])
        setup = parts[3]
        db.update_trade_metrics(trade_id, setup_type=setup)
        await query.edit_message_text(f"✅ Сетап сохранён: {setup}")
    elif data == "cancel_setup":
        await query.edit_message_text("Выбор сетапа отменён.")


async def generate_ai_review(query, trade_id):
    trade = db.find_trade_by_id(trade_id)
    if not trade:
        await query.edit_message_text("❌ Сделка не найдена.")
        return
    prompt = (
        f"Дай краткую оценку сделке (2-3 предложения): что хорошо, что плохо, оценка от 1 до 10.\n"
        f"Символ: {trade['symbol']}, сторона: {trade['side']}, вход: {trade['entry_price']}, "
        f"выход: {trade['exit_price']}, плечо: {trade.get('leverage', 1)}, PNL: {trade['realized_pnl']:.2f}.\n"
        f"Причина входа: {trade.get('entry_comment', 'не указана')}."
    )
    review = ai_analyzer.analyze_raw(prompt)
    db.update_trade_metrics(trade_id, ai_review=review)
    await query.edit_message_text(f"🤖 *AI-оценка сделки #{trade_id}:*\n\n{review}", parse_mode='Markdown')