"""
handlers/menu.py
Main menu handler — routes button presses to appropriate handlers.
"""

from telegram import Update
from telegram.ext import ContextTypes
from core.container import get_db
from core.keyboards import (
    main_menu_keyboard, trading_menu_keyboard, ai_menu_keyboard,
    BTN_TRADING, BTN_AI, BTN_JOURNAL, BTN_HELP,
    BTN_BALANCE, BTN_LAST_TRADES, BTN_STATS, BTN_AI_ANALYSIS,
    BTN_BACK, BTN_CANCEL,
    BTN_CONSILIUM, CONSILIUM_OPEN, CONSILIUM_SETUP,
    BTN_AI_MARKET, BTN_AI_TRENDS, BTN_AI_LEARN, BTN_AI_COACH, BTN_TRADER_DNA,
    NAV_BUTTONS,
)
from core.user_context import require_auth, get_current_user_id
from handlers.trading import show_balance, show_last_trades, show_stats, show_ai_analysis
from handlers.ai import (
    show_market_overview, show_trends, show_journal_analysis, show_coach, show_trader_dna,
    consilium_menu, consilium_open_positions, consilium_analyze_position,
    consilium_new_setup, consilium_process_setup, consilium_keyboard,
)
from handlers.journal import show_journal
from handlers.onboarding import handle_awaiting_bingx_key, handle_awaiting_bingx_secret
from handlers.risk_profile import (
    RISK_ONBOARDING_STATES, handle_awaiting_risk_level, handle_awaiting_trading_style,
    handle_awaiting_experience_level, handle_awaiting_risk_goal,
)
from handlers.system import show_help
from services.comment_manager import save_comment


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip()
    state = context.user_data.get('state')

    # Онбординг BingX-ключей (handlers/onboarding.py, /setkeys) открыт
    # независимо от подписки — обрабатываем ДО require_auth, иначе
    # пользователь без подписки не смог бы ввести свои ключи в принципе.
    if state in ('awaiting_bingx_key', 'awaiting_bingx_secret'):
        if text == BTN_CANCEL or text.strip().lower() in ('отмена', 'cancel'):
            context.user_data['state'] = None
            context.user_data.pop('pending_bingx_api_key', None)
            await update.message.reply_text("Отменено.", reply_markup=main_menu_keyboard())
            return
        if state == 'awaiting_bingx_key':
            await handle_awaiting_bingx_key(update, context)
        else:
            await handle_awaiting_bingx_secret(update, context)
        return

    if not await require_auth(update, context):
        return
    db    = get_db()

    # Промпты entering_comment_inline/entering_exit_reason/entering_entry_reason
    # теперь используют ForceReply (см. core/router.py) вместо кнопки "Отмена"
    # на клавиатуре — её больше не видно на экране, поэтому отмену принимаем
    # и по обычному набранному тексту "отмена"/"cancel".
    if text == BTN_CANCEL or text.strip().lower() in ('отмена', 'cancel'):
        if state == 'consilium_setup_input':
            context.user_data['state'] = None
            await update.message.reply_text("Отменено.", reply_markup=ai_menu_keyboard())
            return
        if state in ('entering_comment_inline', 'entering_exit_reason', 'entering_entry_reason'):
            context.user_data['state'] = None
            context.user_data.pop('comment_order_id', None)
            context.user_data.pop('entry_order_id', None)
            await update.message.reply_text("Отменено.", reply_markup=trading_menu_keyboard())
            return
        if state in RISK_ONBOARDING_STATES:
            context.user_data['state'] = None
            context.user_data.pop('risk_profile_draft', None)
            await update.message.reply_text("Отменено.", reply_markup=main_menu_keyboard())
            return

    if state in ('entering_comment_inline', 'entering_exit_reason', 'entering_entry_reason'):
        if text in NAV_BUTTONS or text.startswith("🏠 *"):
            await update.message.reply_text(
                "⚠️ Сейчас жду текст комментария. Напишите его, или напишите 'отмена'."
            )
            return

    if state == 'entering_comment_inline':
        order_id = context.user_data.get('comment_order_id')
        if order_id:
            success = save_comment(order_id, text, user_id=get_current_user_id(context))
            if success:
                await update.message.reply_text(f"✅ Комментарий сохранён для сделки #{order_id}!", reply_markup=trading_menu_keyboard())
            else:
                await update.message.reply_text("❌ Сделка не найдена.", reply_markup=trading_menu_keyboard())
        context.user_data['state'] = None
        context.user_data.pop('comment_order_id', None)
        return

    if state == 'entering_exit_reason':
        trade_id = context.user_data.get('comment_order_id')
        if trade_id:
            # user_id — иначе подписчик, подобрав/угадав чужой числовой
            # trade_id в state (изначально пришедший из callback_data кнопки
            # "Добавить вывод"), мог бы записать текст в чужую закрытую
            # сделку (см. AUDIT.md — запись без проверки владения).
            db.update_trade_metrics(trade_id, user_id=get_current_user_id(context), exit_comment=text)
            await update.message.reply_text("✅ Вывод сохранён.", reply_markup=trading_menu_keyboard())
        context.user_data['state'] = None
        context.user_data.pop('comment_order_id', None)
        return

    if state == 'entering_entry_reason':
        order_id = context.user_data.get('entry_order_id')
        if order_id:
            db.update_open_trade_by_order_id(order_id, user_id=get_current_user_id(context), entry_comment=text)
            await update.message.reply_text("✅ Причина входа сохранена.", reply_markup=trading_menu_keyboard())
        context.user_data['state'] = None
        return

    if state == 'consilium_choose_position':
        # Раньше кнопка "Назад" на клавиатуре выбора позиции (см.
        # consilium_open_positions) уходила прямиком в
        # consilium_analyze_position как будто это название позиции — та не
        # находила совпадения и просила выбрать позицию заново, не сбрасывая
        # state, так что кнопка "Назад" переставала работать насовсем.
        if text == BTN_BACK:
            context.user_data['state'] = None
            await update.message.reply_text(
                "🧠 Консилиум\nВыбери режим:", reply_markup=consilium_keyboard()
            )
            return
        await consilium_analyze_position(update, context)
        return
    if state == 'consilium_setup_input':
        await consilium_process_setup(update, context)
        return
    if state == 'awaiting_risk_level':
        await handle_awaiting_risk_level(update, context)
        return
    if state == 'awaiting_trading_style':
        await handle_awaiting_trading_style(update, context)
        return
    if state == 'awaiting_experience_level':
        await handle_awaiting_experience_level(update, context)
        return
    if state == 'awaiting_risk_goal':
        await handle_awaiting_risk_goal(update, context)
        return

    if text == BTN_TRADING:
        await update.message.reply_text("📈 *Trading*\nВыбери действие:", parse_mode='Markdown', reply_markup=trading_menu_keyboard())
    elif text == BTN_AI:
        await update.message.reply_text("🤖 *AI-Ассистент*\nВыбери, что хочешь проанализировать:", parse_mode='Markdown', reply_markup=ai_menu_keyboard())
    elif text == BTN_JOURNAL:
        await show_journal(update, context)
    elif text == BTN_HELP:
        await show_help(update)
    elif text == BTN_BACK:
        await update.message.reply_text("🏠 *Главное меню*\nВыбери раздел:", parse_mode='Markdown', reply_markup=main_menu_keyboard())
    elif text == BTN_BALANCE:
        await show_balance(update, context)
    elif text == BTN_LAST_TRADES:
        await show_last_trades(update, context)
    elif text == BTN_STATS:
        await show_stats(update, context)
    elif text == BTN_AI_ANALYSIS:
        await show_ai_analysis(update, context)
    elif text == BTN_CONSILIUM:
        await consilium_menu(update)
    elif text == CONSILIUM_OPEN:
        await consilium_open_positions(update, context)
    elif text == CONSILIUM_SETUP:
        await consilium_new_setup(update, context)
    elif text == BTN_AI_MARKET:
        await show_market_overview(update)
    elif text == BTN_AI_TRENDS:
        await show_trends(update)
    elif text == BTN_AI_LEARN:
        await show_journal_analysis(update, context)
    elif text == BTN_AI_COACH:
        await show_coach(update, context)
    elif text == BTN_TRADER_DNA:
        await show_trader_dna(update, context)
    else:
        await update.message.reply_text("Используй кнопки меню 👇", reply_markup=main_menu_keyboard())
