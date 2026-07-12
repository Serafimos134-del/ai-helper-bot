"""
handlers/subscription.py
Мультитенантность, Этап 4 (см. MULTITENANCY_MIGRATION_PLAN.md) — оплата
подписки через Crypto Pay. Новый пользователь получает TRIAL_PERIOD_DAYS
бесплатно автоматически (services/database.py:get_or_create_user);
/subscribe нужен для продления после окончания триала/оплаченного периода
и открыт независимо от текущего статуса подписки — иначе пользователь с
истёкшей подпиской не смог бы её продлить.

Тарифная сетка — core/billing.py:SUBSCRIPTION_PLANS. /subscribe показывает
все планы кнопками (callback_data="sub_{plan_id}"), core/router.py
диспетчерит выбор сюда же (handle_plan_selected).
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.billing import SUBSCRIPTION_PLANS, SUBSCRIPTION_ASSET
from core.container import get_db
from core.user_context import get_current_user_id
from services.crypto_pay import create_invoice

logger = logging.getLogger(__name__)


def _plans_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for plan_id, plan in SUBSCRIPTION_PLANS.items():
        rows.append([InlineKeyboardButton(
            f"{plan['label']} — {plan['price']} {SUBSCRIPTION_ASSET}",
            callback_data=f"sub_{plan_id}"
        )])
    return InlineKeyboardMarkup(rows)


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 Выбери тариф:",
        reply_markup=_plans_keyboard()
    )


async def handle_plan_selected(query, context: ContextTypes.DEFAULT_TYPE, plan_id: str):
    plan = SUBSCRIPTION_PLANS.get(plan_id)
    if not plan:
        await query.edit_message_text("❌ Неизвестный тариф. Попробуй ещё раз: /subscribe")
        return

    db = get_db()
    user_id = get_current_user_id(context)

    result = await create_invoice(
        amount=plan['price'],
        description=f"Подписка AI Helper Bot — {plan['label']}",
        payload=user_id,
        asset=SUBSCRIPTION_ASSET,
    )
    if not result.get('success'):
        await query.edit_message_text(
            f"❌ Не удалось создать счёт на оплату: {result.get('error', 'неизвестная ошибка')}.\n"
            f"Попробуй ещё раз: /subscribe"
        )
        return

    try:
        db.create_payment(result['invoice_id'], user_id, plan['price'], SUBSCRIPTION_ASSET, days=plan['days'])
    except Exception as e:
        logger.error(f"handle_plan_selected: не удалось сохранить платёж {result['invoice_id']} для {user_id}: {e}")
        await query.edit_message_text("❌ Внутренняя ошибка, попробуй ещё раз: /subscribe")
        return

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Оплатить", url=result['pay_url'])]])
    await query.edit_message_text(
        f"💳 {plan['label']} — {plan['price']} {SUBSCRIPTION_ASSET}.\n\n"
        f"После оплаты доступ продлится автоматически (проверяю в течение минуты).",
        reply_markup=keyboard
    )
