"""
handlers/risk_profile.py
Персональная модель риска (задача от 12.07.2026):
- /riskprofile — заявленный пользователем профиль (4 коротких шага кнопками,
  тот же диалоговый паттерн, что handlers/onboarding.py для BingX-ключей).
- /riskscore — фактический Risk Score из реальных сделок
  (ai/risk_profile.py:compute_risk_score) + сравнение с заявленным профилем.

В отличие от /setkeys (открыт всем, часть онбординга до подписки),
риск-профиль имеет смысл только для уже допущенного пользователя — гейт
через require_auth(), как большинство фич.
"""

import asyncio
import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes

from core.container import get_db
from core.keyboards import main_menu_keyboard, BTN_CANCEL
from core.user_context import get_current_user_id, require_auth

logger = logging.getLogger(__name__)

RISK_LEVEL_OPTIONS = {
    '🛡 Консервативный': 'conservative',
    '⚖️ Сбалансированный': 'balanced',
    '🚀 Агрессивный': 'aggressive',
}
TRADING_STYLE_OPTIONS = {
    '⚡ Скальпинг': 'scalping',
    '📅 Внутри дня': 'intraday',
    '📈 Свинг': 'swing',
    '🏔 Долгосрочно': 'long_term',
}
EXPERIENCE_OPTIONS = {
    '🌱 Новичок': 'beginner',
    '📊 Средний уровень': 'intermediate',
    '🎓 Продвинутый': 'advanced',
}
GOAL_OPTIONS = {
    '🔒 Сохранение капитала': 'capital_preservation',
    '📈 Стабильный рост': 'steady_growth',
    '🚀 Максимальная доходность': 'maximum_return',
}

RISK_ONBOARDING_STATES = (
    'awaiting_risk_level', 'awaiting_trading_style',
    'awaiting_experience_level', 'awaiting_risk_goal',
)


def _kb(options: dict) -> ReplyKeyboardMarkup:
    rows = [[label] for label in options.keys()]
    rows.append([BTN_CANCEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def riskprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return
    context.user_data['state'] = 'awaiting_risk_level'
    context.user_data['risk_profile_draft'] = {}
    await update.message.reply_text(
        "🧭 Настроим твой риск-профиль (4 коротких шага).\n\n"
        "1/4. Какой у тебя риск-подход?",
        reply_markup=_kb(RISK_LEVEL_OPTIONS)
    )


async def handle_awaiting_risk_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    value = RISK_LEVEL_OPTIONS.get(text)
    if not value:
        await update.message.reply_text("Выбери один из вариантов на клавиатуре 👇", reply_markup=_kb(RISK_LEVEL_OPTIONS))
        return
    context.user_data.setdefault('risk_profile_draft', {})['risk_level'] = value
    context.user_data['state'] = 'awaiting_trading_style'
    await update.message.reply_text("2/4. Какой у тебя стиль торговли?", reply_markup=_kb(TRADING_STYLE_OPTIONS))


async def handle_awaiting_trading_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    value = TRADING_STYLE_OPTIONS.get(text)
    if not value:
        await update.message.reply_text("Выбери один из вариантов на клавиатуре 👇", reply_markup=_kb(TRADING_STYLE_OPTIONS))
        return
    context.user_data.setdefault('risk_profile_draft', {})['trading_style'] = value
    context.user_data['state'] = 'awaiting_experience_level'
    await update.message.reply_text("3/4. Какой у тебя опыт торговли?", reply_markup=_kb(EXPERIENCE_OPTIONS))


async def handle_awaiting_experience_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    value = EXPERIENCE_OPTIONS.get(text)
    if not value:
        await update.message.reply_text("Выбери один из вариантов на клавиатуре 👇", reply_markup=_kb(EXPERIENCE_OPTIONS))
        return
    context.user_data.setdefault('risk_profile_draft', {})['experience_level'] = value
    context.user_data['state'] = 'awaiting_risk_goal'
    await update.message.reply_text("4/4. Какая у тебя цель?", reply_markup=_kb(GOAL_OPTIONS))


async def handle_awaiting_risk_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    value = GOAL_OPTIONS.get(text)
    if not value:
        await update.message.reply_text("Выбери один из вариантов на клавиатуре 👇", reply_markup=_kb(GOAL_OPTIONS))
        return
    draft = context.user_data.setdefault('risk_profile_draft', {})
    draft['risk_goal'] = value
    context.user_data['state'] = None

    db = get_db()
    user_id = get_current_user_id(context)
    try:
        db.set_risk_profile(
            user_id,
            risk_level=draft.get('risk_level'),
            trading_style=draft.get('trading_style'),
            experience_level=draft.get('experience_level'),
            risk_goal=draft.get('risk_goal'),
        )
        db.complete_risk_onboarding(user_id)
    except Exception as e:
        logger.error(f"riskprofile: не удалось сохранить профиль для {user_id}: {e}")
        await update.message.reply_text("❌ Не удалось сохранить профиль, попробуй ещё раз: /riskprofile")
        return
    context.user_data.pop('risk_profile_draft', None)

    await update.message.reply_text(
        "✅ Риск-профиль сохранён!\n\n"
        "Как только накопится минимум 5 закрытых сделок, я смогу сравнить твой заявленный "
        "профиль с фактическим поведением на бирже — команда /riskscore.",
        reply_markup=main_menu_keyboard()
    )


async def riskscore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return
    from ai.risk_profile import compute_risk_score, compare_declared_vs_actual

    db = get_db()
    user_id = get_current_user_id(context)
    msg = await update.message.reply_text("📊 Считаю Risk Score по твоим сделкам...")

    try:
        result = await asyncio.wait_for(compute_risk_score(db, user_id), timeout=25)
    except asyncio.TimeoutError:
        await msg.edit_text("❌ Не дождался ответа от биржи/БД. Попробуй ещё раз чуть позже: /riskscore")
        return

    if result['score'] is None:
        await msg.edit_text(
            f"Недостаточно данных для расчёта — нужно минимум 5 закрытых сделок "
            f"(сейчас {result['total_trades']})."
        )
        return

    try:
        db.save_risk_score(user_id, result['score'], result['components'])
    except Exception as e:
        logger.error(f"riskscore: не удалось сохранить risk_score для {user_id}: {e}")

    d = result['details']
    c = result['components']
    lines = [
        f"📊 *Risk Score: {result['score']}/100 ({result['label']})*",
        f"Уверенность: {result['confidence']} ({result['total_trades']} сделок)\n",
        "*Факторы:*",
        f"⚡️ Плечо — {c['leverage']}/100 (в среднем {d['avg_leverage']}x)",
    ]
    if d['position_exposure_pct'] is not None:
        lines.append(f"📐 Размер позиции — {c['position_size']}/100 (экспозиция {d['position_exposure_pct']}% от депозита)")
    else:
        lines.append(f"📐 Размер позиции — {c['position_size']}/100 (нет открытых позиций сейчас)")
    if d['max_drawdown_pct'] is not None:
        lines.append(f"📉 Просадка — {c['drawdown']}/100 (максимум {d['max_drawdown_pct']}% от баланса)")
    else:
        lines.append(f"📉 Просадка — {c['drawdown']}/100")
    if d['stop_loss_discipline_rate'] is not None:
        lines.append(f"🛑 Дисциплина стопов — {c['stop_loss_discipline']}/100 (SL+TP в {d['stop_loss_discipline_rate']}% сделок)")
    lines.append(f"🔁 Переторговля — {c['overtrading']}/100 ({d['overtrading_events_30d']} случаев за 30 дней)")
    if d['avg_dca_count'] is not None:
        lines.append(f"➕ Усреднения — {c['dca_behavior']}/100 (в среднем {d['avg_dca_count']} на сделку)")

    profile = db.get_risk_profile(user_id)
    if profile and profile.get('risk_level'):
        cmp = compare_declared_vs_actual(profile['risk_level'], result)
        if cmp['mismatch']:
            lines.append(f"\n{cmp['text']}")
        else:
            lines.append("\n✅ Фактический риск соответствует заявленному профилю.")
    else:
        lines.append("\n💡 Заполни риск-профиль (/riskprofile), чтобы сравнить заявленный и фактический риск.")

    await msg.edit_text("\n".join(lines), parse_mode='Markdown', reply_markup=main_menu_keyboard())
