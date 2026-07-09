"""
handlers/ai.py
AI-related handlers: market overview, trends, journal analysis, consilium, coach.
"""

import json
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from core.container import get_ai_analyzer, get_orchestrator, get_db
from core.keyboards import ai_menu_keyboard, cancel_keyboard, BTN_BACK, CONSILIUM_OPEN, CONSILIUM_SETUP
from services.bingx_api import get_top_tickers, get_kline, get_open_positions
from utils.telegram_text import clean_markdown as _clean


async def _send_chunks(obj, text: str, **kwargs):
    """Разбивает длинный текст на куски по 4000 символов и отправляет."""
    limit = 4000
    for i in range(0, len(text), limit):
        await obj.reply_text(text[i:i+limit], **kwargs)


async def show_market_overview(update: Update):
    ai_analyzer = get_ai_analyzer()
    msg = await update.message.reply_text("🌐 Собираю данные рынка...")
    result = await get_top_tickers(10)
    if not result.get('success') or not result.get('tickers'):
        await msg.delete()
        await update.message.reply_text(
            f"❌ Не удалось получить данные рынка: {result.get('error', 'нет данных')}",
            reply_markup=ai_menu_keyboard()
        )
        return
    summary = []
    for t in result['tickers']:
        symbol = t.get('symbol', '')
        change = float(t.get('priceChangePercent', 0))
        volume = float(t.get('quoteVolume', 0))
        summary.append(f"{symbol}: изм {change:+.2f}%, объём {volume:,.0f}")
    prompt = (
        "Проанализируй рыночную ситуацию на основе данных топ-10 криптовалют по объёму за 24 часа:\n"
        + "\n".join(summary)
        + "\n\nТвой ответ должен содержать строго:\n"
        + "1. ОБЩИЙ НАСТРОЙ: (бычий/медвежий/нейтральный) — одним предложением\n"
        + "2. ТОП-3 МОНЕТЫ С СИЛЬНЕЙШИМ ДВИЖЕНИЕМ (рост и падение) — назови и возможные причины\n"
        + "3. ВОЗМОЖНЫЕ ТОЧКИ ВХОДА: любые две монеты из списка с кратким обоснованием\n\n"
        + "Будь конкретен, используй цифры из данных выше. Без философии и общих фраз."
    )
    try:
        analysis = _clean(await ai_analyzer.analyze_raw(prompt))
    except Exception as e:
        analysis = f"Ошибка AI: {e}"
    try:
        await msg.delete()
    except Exception:
        pass
    await _send_chunks(update.message, f"🌐 Обзор рынка от AI\n\n{analysis}", reply_markup=ai_menu_keyboard())


async def show_trends(update: Update):
    ai_analyzer = get_ai_analyzer()
    msg = await update.message.reply_text("📊 Анализирую тренды...")
    symbols = ["BTC-USDT", "ETH-USDT"]
    data_lines = []
    for sym in symbols:
        result = await get_kline(sym, "1h", 24)
        klines = result.get('klines', [])
        if result.get('success') and len(klines) >= 2:
            try:
                closes = [float(k.get('close', k.get('c', 0))) for k in klines]
                highs  = [float(k.get('high',  k.get('h', 0))) for k in klines]
                lows   = [float(k.get('low',   k.get('l', 0))) for k in klines]
                first_close = closes[0]
                last_close  = closes[-1]
                if first_close:
                    change = (last_close - first_close) / first_close * 100
                    data_lines.append(
                        f"{sym}: изменение за 24ч {change:+.2f}%, "
                        f"максимум {max(highs):.2f}, минимум {min(lows):.2f}, "
                        f"текущая цена {last_close:.2f}"
                    )
            except (ValueError, TypeError, AttributeError):
                continue
    if not data_lines:
        try:
            await msg.delete()
        except Exception:
            pass
        await update.message.reply_text("❌ Не удалось получить данные по трендам.", reply_markup=ai_menu_keyboard())
        return
    prompt = (
        "Тренд-анализ на основе часовых свечей за 24 часа:\n"
        + "\n".join(data_lines)
        + "\n\nТвой ответ дай строго в формате:\n"
        + "1. BTC: тренд (восходящий/нисходящий/боковик), ключевые уровни поддержки и сопротивления на сегодня\n"
        + "2. ETH: аналогично\n"
        + "3. СИГНАЛ: если видишь явную точку входа по любой из монет — укажи направление, цену входа и стоп-лосс. Если явного сигнала нет — напиши «явного сигнала нет»\n\n"
        + "Кратко, без воды, используй цифры из данных выше."
    )
    try:
        analysis = _clean(await ai_analyzer.analyze_raw(prompt))
    except Exception as e:
        analysis = f"Ошибка AI: {e}"
    try:
        await msg.delete()
    except Exception:
        pass
    await _send_chunks(update.message, f"📊 Тренды от AI\n\n{analysis}", reply_markup=ai_menu_keyboard())


async def show_journal_analysis(update: Update):
    db = get_db()
    ai_analyzer = get_ai_analyzer()
    msg = await update.message.reply_text("🤖 Анализирую журнал сделок...")
    trades = db.get_closed_trades(limit=50)
    if not trades:
        await msg.edit_text("Нет закрытых сделок для анализа.")
        return
    data_for_ai = []
    for t in trades:
        data_for_ai.append({
            'symbol':        t['symbol'],
            'side':          t['side'],
            'entry_price':   t['entry_price'],
            'exit_price':    t['exit_price'],
            'pnl':           t['realized_pnl'],
            'leverage':      t.get('leverage', 1),
            'stop_loss':     t.get('stop_loss'),
            'take_profit':   t.get('take_profit'),
            'entry_comment': t.get('entry_comment', ''),
            'exit_comment':  t.get('exit_comment', t.get('comment', ''))
        })
    trades_text = json.dumps(data_for_ai, ensure_ascii=False, indent=2)
    prompt = (
        "Проанализируй журнал сделок трейдера. "
        "Выдели повторяющиеся паттерны, главные ошибки в риск-менеджменте, "
        "психологические ловушки и сильные стороны. "
        "Дай конкретные рекомендации по улучшению стратегии и дисциплины. "
        "Пиши без markdown-форматирования — только чистый текст.\n\n"
        f"Журнал сделок:\n{trades_text}"
    )
    answer = _clean(await ai_analyzer.analyze_raw(prompt, max_tokens=1500))
    try:
        await msg.delete()
    except Exception:
        pass
    await _send_chunks(update.message, f"📊 Анализ журнала:\n\n{answer}", reply_markup=ai_menu_keyboard())


async def show_coach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI Coach — персональный разбор на основе Performance Engine."""
    from services.coach_engine import CoachEngine
    ai_analyzer = get_ai_analyzer()
    db = get_db()

    msg = await update.message.reply_text("🎯 Готовлю персональный разбор...")

    if not ai_analyzer.provider:
        await msg.edit_text("⚠️ AI недоступен. Проверь GROQ_API_KEY.")
        return

    coach = CoachEngine(ai_analyzer.provider, db)
    result = await coach.generate_coaching(user_id='default')
    text = _clean(result)

    try:
        await msg.delete()
    except Exception:
        pass

    await _send_chunks(update.message, f"🎯 AI Coach\n\n{text}", reply_markup=ai_menu_keyboard())


# ══════════════════════════════════════════════════════════════════════════════
# КОНСИЛИУМ
# ══════════════════════════════════════════════════════════════════════════════

def consilium_keyboard():
    return ReplyKeyboardMarkup([[CONSILIUM_OPEN], [CONSILIUM_SETUP], [BTN_BACK]], resize_keyboard=True)


async def consilium_menu(update: Update):
    await update.message.reply_text(
        "🧠 Консилиум\nВыбери режим:",
        reply_markup=ReplyKeyboardMarkup([[CONSILIUM_OPEN], [CONSILIUM_SETUP], [BTN_BACK]], resize_keyboard=True)
    )


async def consilium_open_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = await get_open_positions()
    if not res.get('success') or not res.get('trades'):
        await update.message.reply_text("Нет открытых позиций или ошибка API.", reply_markup=consilium_keyboard())
        return
    trades = res['trades']
    context.user_data['consilium_positions'] = trades
    keyboard = []
    for t in trades:
        sym      = t['symbol']
        raw_side = str(t.get('side', '')).upper()
        side     = 'LONG' if raw_side in ('BUY', 'LONG') else 'SHORT'
        keyboard.append([f"{sym} {side}"])
    keyboard.append([BTN_BACK])
    await update.message.reply_text(
        "Выбери позицию для анализа:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    context.user_data['state'] = 'consilium_choose_position'


async def consilium_analyze_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orchestrator = get_orchestrator()
    text      = update.message.text.strip()
    trades    = context.user_data.get('consilium_positions', [])
    chosen    = None
    expected_side = ''
    for t in trades:
        raw_side      = str(t.get('side', '')).upper()
        expected_side = 'LONG' if raw_side in ('BUY', 'LONG') else 'SHORT'
        if f"{t['symbol']} {expected_side}" == text:
            chosen = t
            break
    if not chosen:
        await update.message.reply_text("Выбери позицию из списка.", reply_markup=consilium_keyboard())
        return
    context.user_data['state'] = None
    msg    = await update.message.reply_text("🔄 Анализирую позицию...")
    result = await orchestrator.review_open_position(chosen)
    # Добавляем SL/TP из выбранной позиции в результат для отображения
    result['stop_loss'] = chosen.get('stopLoss') or chosen.get('stop_loss')
    result['take_profit'] = chosen.get('takeProfit') or chosen.get('take_profit')
    response = _build_response(result, chosen['symbol'], expected_side)
    await msg.edit_text(response[:4096])
    await update.message.reply_text("Что дальше?", reply_markup=consilium_keyboard())


async def consilium_new_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 Опишите сетап в свободной форме.\nПримеры:\n• SOL long\n• BTC short\n• Думаю открыть ETH long",
        reply_markup=cancel_keyboard()
    )
    context.user_data['state'] = 'consilium_setup_input'


async def consilium_process_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.parsers import parse_trade_idea
    orchestrator     = get_orchestrator()
    text             = update.message.text.strip()
    ticker, direction = parse_trade_idea(text)
    if not ticker or not direction:
        await update.message.reply_text(
            "Не удалось определить тикер и направление. Укажите явно, например: BTC long",
            reply_markup=cancel_keyboard()
        )
        return
    context.user_data['state'] = None
    msg    = await update.message.reply_text("🔄 Анализирую сетап...")
    result = await orchestrator.evaluate_new_setup(ticker, direction, extra_notes=text)
    response = _build_response(result, ticker, direction)
    await msg.edit_text(response[:4096])
    await update.message.reply_text("Что дальше?", reply_markup=consilium_keyboard())


def _build_response(result: dict, ticker: str, direction: str) -> str:
    response = (
        f"🧠 Консилиум — {ticker} {direction}\n\n"
        f"📈 Рынок:\n{result.get('market_review', '—')}\n\n"
        f"⚠️ Риск:\n{result.get('risk_review', '—')}\n\n"
        f"🧘 Психология:\n{result.get('psychology_review', '—')}\n\n"
    )
    verdict_str = result.get('judge_verdict', '{}')
    try:
        verdict         = json.loads(verdict_str) if isinstance(verdict_str, str) else verdict_str
        verdict_text    = verdict.get('verdict', '—')
        final_score     = verdict.get('final_score', '—')
        verdict_summary = verdict.get('summary', '')
        warnings        = verdict.get('warnings', [])
        emoji_map       = {'STRONG_ENTER': '🟢', 'ENTER': '🟢', 'WAIT': '🟡', 'AVOID': '🔴'}
        emoji = emoji_map.get(verdict_text, '⚪')
        response += f"⚖️ Вердикт: {emoji} {verdict_text} ({final_score}/100)\n"
        if verdict_summary:
            response += f"{verdict_summary}\n"
        if warnings:
            response += "\n⚠️ Предупреждения:\n"
            for w in warnings:
                response += f"• {w}\n"
    except Exception:
        response += f"⚖️ Вердикт: {verdict_str}"

    confidence   = result.get('confidence')
    data_quality = result.get('data_quality')
    disagreement = result.get('disagreement')
    if confidence is not None:
        response += f"\n📊 Уверенность: {confidence:.0%}"
        if data_quality is not None:
            response += f" | Качество данных: {data_quality:.0%}"
        if disagreement is not None:
            response += f" | Разногласия: {disagreement:.0%}"

    # Показываем SL/TP, если они есть в результате (для открытых позиций)
    sl = result.get('stop_loss')
    tp = result.get('take_profit')
    if sl:
        response += f"\n🛑 SL: ${float(sl):.4f}"
    if tp:
        response += f"\n🎯 TP: ${float(tp):.4f}"

    plan = result.get('position_plan')
    if plan and plan.get('decision') and plan['decision'] != 'UNKNOWN':
        decision_emoji = {
            'HOLD': '✅', 'EXIT': '🚪', 'DCA': '➕',
            'PARTIAL_TP': '💰', 'FULL_TP': '🏁',
        }.get(plan['decision'], '❔')
        response += f"\n\n{decision_emoji} Решение по позиции: {plan['decision']}\n{plan.get('reason', '')}"

        details = plan.get('details', {})
        stop = details.get('stop', {})
        tp_data = details.get('tp', {})

        if stop.get('hard_sl'):
            response += f"\n🛑 Hard SL (инвалидация): ${stop['hard_sl']:.4f}"
        if stop.get('status') not in (None, 'keep', 'exit') and stop.get('recommended_sl'):
            response += f"\n🔧 Перенести стоп на: ${stop['recommended_sl']:.4f} ({stop.get('reason', '')})"
        if tp_data.get('tp1'):
            tp_line = f"\n🎯 TP1: ${tp_data['tp1']:.4f}"
            if tp_data.get('tp2'):
                tp_line += f" | TP2: ${tp_data['tp2']:.4f}"
            response += tp_line

    trade_plan = result.get('trade_plan')
    if trade_plan and trade_plan.get('entry_price'):
        response += f"\n\n📋 Торговый план:\n💵 Вход: ${trade_plan['entry_price']:.4f}"
        if trade_plan.get('stop_loss'):
            response += f"\n🛑 SL: ${trade_plan['stop_loss']:.4f}"
        tp_parts = [
            f"TP{i} ${trade_plan[key]:.4f}"
            for i, key in enumerate(('tp1', 'tp2', 'tp3'), start=1)
            if trade_plan.get(key)
        ]
        if tp_parts:
            response += "\n🎯 " + " | ".join(tp_parts)
        if trade_plan.get('risk_reward'):
            response += f"\n⚖️ Risk/Reward: 1:{trade_plan['risk_reward']}"
        if trade_plan.get('position_size'):
            base = trade_plan['symbol'].split('-')[0]
            response += (
                f"\n📊 Размер позиции: {trade_plan['position_size']} {base} "
                f"(плечо {trade_plan['leverage']}x, маржа ${trade_plan['margin']:,.2f})"
            )

    memory = result.get('memory')
    if memory:
        response += f"\n\n{memory}"
    return response