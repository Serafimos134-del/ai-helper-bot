import json
import logging
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
)
from core.container import get_orchestrator, get_ai_analyzer, get_db
from utils.formatting import format_verdict, format_score_breakdown
from utils.telegram_text import clean_markdown, strip_llm_self_correction

logger = logging.getLogger(__name__)

# Раньше здесь стояло db = Database() и ai_analyzer = AITradingAnalyzer() —
# для Database разницы нет (это синглтон), а вот AITradingAnalyzer им не
# является, так что создавался второй, отдельный от контейнера экземпляр
# (лишний GroqProvider). Теперь используем те же объекты, что и остальной код.
db = get_db()
ai_analyzer = get_ai_analyzer()


def setup_router(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(handle_callback))


def _fmt_date(iso_str: str) -> str:
    """Превращает ISO-дату в читаемый вид: '2026-07-01 01:13'."""
    if not iso_str:
        return "—"
    try:
        clean = re.sub(r"\.\d+", "", iso_str.replace("T", " ").rsplit("+", 1)[0].strip())
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:16] if len(iso_str) >= 16 else iso_str


def _fmt_price(val) -> str:
    """Безопасное форматирование цены."""
    if val is None:
        return "—"
    try:
        return f"${float(val):.4f}"
    except (ValueError, TypeError):
        return str(val)


def _current_user_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    """user_id текущего пользователя, зарезолвленного middleware
    (core/user_context.py, устанавливается в group=-1 до этого хендлера).
    Нужен, чтобы find_trade_by_id/add_comment не отдавали чужую сделку по
    trade_id из callback_data (простое число, потенциально подбираемое) —
    см. MULTITENANCY_MIGRATION_PLAN.md, "разграничение данных"."""
    user = context.user_data.get('user') if context.user_data else None
    return user['user_id'] if user else 'default'


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        await _dispatch_callback(data, query, context)
    except Exception as e:
        # Раньше исключение здесь (например, Telegram отказывался парсить
        # Markdown из-за непарного */_ в пользовательском комментарии)
        # уходило только в глобальный error_handler (bot.py), который лишь
        # логирует — пользователь не получал вообще никакого ответа, и
        # кнопка выглядела так, будто она "не нажимается".
        logger.error(f"Ошибка обработки callback '{data}': {e}", exc_info=True)
        try:
            await query.edit_message_text(f"❌ Ошибка: {e}")
        except Exception as inner_e:
            logger.error(f"Не удалось сообщить об ошибке callback '{data}': {inner_e}")


async def _dispatch_callback(data: str, query, context: ContextTypes.DEFAULT_TYPE) -> None:
    if data.startswith("comment_"):
        parts = data.split("_", 1)
        if len(parts) < 2:
            return
        trade_id = int(parts[1])
        context.user_data['comment_order_id'] = trade_id
        context.user_data['state'] = 'entering_comment_inline'
        # editMessageText принимает только InlineKeyboardMarkup — попытка
        # прикрепить сюда ReplyKeyboardMarkup (cancel_keyboard()) роняла весь
        # вызов ошибкой Telegram API, и промпт для ввода комментария вообще
        # не показывался (см. try/except в handle_callback). ForceReply
        # можно отправить только новым сообщением, поэтому редактируем
        # исходный текст отдельно, а поле ввода открываем новым сообщением.
        await query.edit_message_text(f"✏️ Комментарий к сделке #{trade_id}")
        await query.message.reply_text(
            "Напишите комментарий (или 'отмена'):",
            reply_markup=ForceReply(input_field_placeholder="Комментарий к сделке")
        )
    elif data.startswith("detail_"):
        parts = data.split("_", 1)
        if len(parts) < 2:
            return
        trade_id = int(parts[1])
        trade = db.find_trade_by_id(trade_id, user_id=_current_user_id(context))
        if trade:
            holding = trade.get('holding_minutes')
            duration_str = f"{holding} мин" if holding is not None else "—"
            sl_line = f"\n🛑 Стоп: {_fmt_price(trade.get('stop_loss'))}" if trade.get('stop_loss') else ""
            tp_line = f"\n🎯 Тейк: {_fmt_price(trade.get('take_profit'))}" if trade.get('take_profit') else ""
            close_time = _fmt_date(trade.get('close_time') or trade.get('closed_at'))
            # Без parse_mode: exit_comment/comment — свободный пользовательский
            # текст, непарный */_ в нём ломает Markdown-парсинг Telegram, и
            # edit_message_text падает с BadRequest (см. try/except в
            # handle_callback — кнопка "детали сделки" именно из-за этого
            # выглядела так, будто не реагирует на нажатие).
            detail_text = (
                f"📊 Детали сделки #{trade_id}\n\n"
                f"Символ: {trade['symbol']}\n"
                f"Сторона: {trade['side']}\n"
                f"Вход: ${trade['entry_price']:.4f}\n"
                f"Выход: ${trade['exit_price']:.4f}\n"
                f"Объём: {trade['quantity']}\n"
                f"Плечо: {trade.get('leverage', 1)}x\n"
                f"Длительность: {duration_str}\n"
                f"PNL: ${trade['realized_pnl']:.2f}"
                f"{sl_line}{tp_line}\n"
                f"Тренд рынка: {trade.get('market_trend') or '—'}\n"
                f"Сетап: {trade.get('setup_type') or '—'}\n"
                f"Комментарий: {trade.get('exit_comment') or trade.get('comment') or '—'}\n"
                f"Закрыта: {close_time}"
            )
            detail_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Добавить комментарий", callback_data=f"comment_{trade_id}"),
                 InlineKeyboardButton("🤖 AI-оценка", callback_data=f"ai_full_{trade_id}")]
            ])
            await query.edit_message_text(detail_text, reply_markup=detail_keyboard)
        else:
            await query.edit_message_text("❌ Сделка не найдена.")
    elif data.startswith("ai_full_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        trade_id = int(parts[2])
        await generate_full_ai_analysis(query, trade_id, _current_user_id(context))
    elif data.startswith("eval_"):
        parts = data.split("_", 1)
        if len(parts) < 2:
            return
        trade_id = int(parts[1])
        await generate_ai_review(query, trade_id, _current_user_id(context))
    elif data.startswith("entry_reason_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            await query.edit_message_text("❌ Ошибка: неверный ID позиции.")
            return
        order_id = parts[2]
        context.user_data['entry_order_id'] = order_id
        context.user_data['state'] = 'entering_entry_reason'
        # См. комментарий у ветки "comment_" — ReplyKeyboardMarkup/ForceReply
        # нельзя передать в edit_message_text, только в новом сообщении.
        await query.edit_message_text("✏️ Причина входа")
        await query.message.reply_text(
            "Напишите причину входа (или 'отмена'):",
            reply_markup=ForceReply(input_field_placeholder="Причина входа")
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
        await query.edit_message_text("✏️ Вывод по сделке")
        await query.message.reply_text(
            "Напишите вывод по сделке — что поняли (или 'отмена'):",
            reply_markup=ForceReply(input_field_placeholder="Вывод по сделке")
        )
    elif data.startswith("ai_review_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
        trade_id = int(parts[2])
        await generate_ai_review(query, trade_id, _current_user_id(context))
    elif data == "skip_comment":
        await query.edit_message_text("Запись сохранена без комментария.")
    elif data.startswith("setup_"):
        parts = data.split("_", 1)
        if len(parts) < 2:
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
        # user_id — trade_id здесь приходит прямо из callback_data кнопки,
        # без проверки владения (см. AUDIT.md); без фильтра по user_id в
        # update_trade_metrics подписчик мог бы перезаписать setup_type
        # чужой сделки, отправив вручную сконструированный callback.
        db.update_trade_metrics(trade_id, user_id=_current_user_id(context), setup_type=setup)
        await query.edit_message_text(f"✅ Сетап сохранён: {setup}")
    elif data == "cancel_setup":
        await query.edit_message_text("Выбор сетапа отменён.")
    elif data.startswith("sub_"):
        from handlers.subscription import handle_plan_selected
        plan_id = data[len("sub_"):]
        await handle_plan_selected(query, context, plan_id)


async def generate_ai_review(query, trade_id, user_id: str = 'default'):
    trade = db.find_trade_by_id(trade_id, user_id=user_id)
    if not trade:
        await query.edit_message_text("❌ Сделка не найдена.")
        return
    prompt = (
        f"Дай краткую оценку сделке (2-3 предложения): что хорошо, что плохо, оценка от 1 до 10.\n"
        f"Символ: {trade['symbol']}, сторона: {trade['side']}, вход: {trade['entry_price']}, "
        f"выход: {trade['exit_price']}, плечо: {trade.get('leverage', 1)}, PNL: {trade['realized_pnl']:.2f}.\n"
        f"Причина входа: {trade.get('entry_comment', 'не указана')}.\n"
        f"Дай сразу финальный вариант — не пиши в ответе черновые мысли, самокоррекции "
        f"или пометки вроде «ошибся»/«на самом деле»/«заменю на»."
    )
    review = strip_llm_self_correction(await ai_analyzer.analyze_raw(prompt))
    db.update_trade_metrics(trade_id, user_id=user_id, ai_review=review)
    # clean_markdown() убирает **bold**/__underline__/`code` из LLM-ответа —
    # тот же паттерн, что уже используется для AI-текста в handlers/ai.py,
    # handlers/system.py, handlers/trading.py. Без него непарные */_ в
    # ответе модели ломают parse_mode='Markdown' и роняют edit_message_text
    # без видимой реакции для пользователя (см. try/except в handle_callback).
    await query.edit_message_text(f"🤖 AI-оценка сделки #{trade_id}:\n\n{clean_markdown(review)}")


async def generate_full_ai_analysis(query, trade_id, user_id: str = 'default'):
    trade = db.find_trade_by_id(trade_id, user_id=user_id)
    if not trade:
        await query.edit_message_text("❌ Сделка не найдена.")
        return
    await query.edit_message_text("🔄 Запускаю полный AI-анализ...")
    try:
        orchestrator = get_orchestrator()
        analysis = await orchestrator.review_closed_trade(trade, user_id=user_id)
        # score_breakdown считает сам AIOrchestrator (см. ai/orchestrator.py).
        # Раньше здесь читался несуществующий ключ 'ai_score' из ответа
        # консилиума (его там никогда не было) — при ручном перезапуске
        # анализа оценка сделки в БД не обновлялась.
        score = analysis['score_breakdown']
        # Сохраняем все метрики, кроме setup_type (если уже был задан вручную, не перезаписываем)
        existing = db.find_trade_by_id(trade_id, user_id=user_id)
        setup = existing.get('setup_type') if existing else None
        db.update_trade_metrics(
            trade_id,
            user_id=user_id,
            market_review=analysis.get('market_review', ''),
            risk_review=analysis.get('risk_review', ''),
            psychology_review=analysis.get('psychology_review', ''),
            judge_verdict=analysis.get('judge_verdict', ''),
            market_trend=analysis.get('market_trend'),
            setup_type=setup,
            ai_score=score['total_score'],
            score_breakdown=json.dumps(score, ensure_ascii=False)
        )
        # Формируем читаемый ответ для пользователя (без Markdown, чтобы избежать ошибок парсинга)
        verdict_line = format_verdict(analysis.get('judge_verdict', '{}'))
        text = (
            f"🧠 AI-разбор сделки #{trade_id}\n\n"
            f"📈 Рынок:\n{analysis.get('market_review', '—')}\n\n"
            f"⚠️ Риск:\n{analysis.get('risk_review', '—')}\n\n"
            f"🧘 Психология:\n{analysis.get('psychology_review', '—')}\n\n"
            f"⚖️ Вердикт: {verdict_line}\n\n"
            f"{format_score_breakdown(score)}\n\n"
            f"📊 Тренд рынка: {analysis.get('market_trend', '—')}\n"
            f"Данные сохранены. Обновите детали сделки, чтобы увидеть изменения."
        )
        await query.edit_message_text(text)
    except Exception as e:
        logger.error(f"Ошибка полного AI-анализа сделки #{trade_id}: {e}")
        await query.edit_message_text(f"❌ Ошибка анализа: {e}")