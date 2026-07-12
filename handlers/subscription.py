"""
handlers/subscription.py
Мультитенантность, Этап 4 (см. MULTITENANCY_MIGRATION_PLAN.md) — оплата
подписки через Crypto Pay. Новый пользователь получает TRIAL_PERIOD_DAYS
бесплатно автоматически (services/database.py:get_or_create_user);
/subscribe нужен для продления после окончания триала/оплаченного периода
и открыт независимо от текущего статуса подписки — иначе пользователь с
истёкшей подпиской не смог бы её продлить.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.billing import SUBSCRIPTION_PRICE_USDT, SUBSCRIPTION_PERIOD_DAYS, SUBSCRIPTION_ASSET
from core.container import get_db
from core.user_context import get_current_user_id
from services.crypto_pay import create_invoice

logger = logging.getLogger(__name__)


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    user_id = get_current_user_id(context)

    result = await create_invoice(
        amount=SUBSCRIPTION_PRICE_USDT,
        description=f"Подписка AI Helper Bot на {SUBSCRIPTION_PERIOD_DAYS} дней",
        payload=user_id,
        asset=SUBSCRIPTION_ASSET,
    )
    if not result.get('success'):
        await update.message.reply_text(
            f"❌ Не удалось создать счёт на оплату: {result.get('error', 'неизвестная ошибка')}.\n"
            f"Попробуй ещё раз чуть позже: /subscribe"
        )
        return

    try:
        db.create_payment(result['invoice_id'], user_id, SUBSCRIPTION_PRICE_USDT, SUBSCRIPTION_ASSET)
    except Exception as e:
        logger.error(f"subscribe_command: не удалось сохранить платёж {result['invoice_id']} для {user_id}: {e}")
        await update.message.reply_text("❌ Внутренняя ошибка, попробуй ещё раз: /subscribe")
        return

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Оплатить", url=result['pay_url'])]])
    await update.message.reply_text(
        f"💳 Подписка на {SUBSCRIPTION_PERIOD_DAYS} дней — {SUBSCRIPTION_PRICE_USDT} {SUBSCRIPTION_ASSET}.\n\n"
        f"После оплаты доступ продлится автоматически (проверяю в течение минуты).",
        reply_markup=keyboard
    )
