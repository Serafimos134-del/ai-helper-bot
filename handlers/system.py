"""
handlers/system.py
System-level handlers: start, help, health, sync, status, ai_fix, test_behavior, calc, setidea, analyze.
"""

import os
import re
import json
from telegram import Update
from telegram.ext import ContextTypes
from core.container import get_db, get_ai_analyzer
from core.keyboards import main_menu_keyboard
from core.billing import SUBSCRIPTION_PRICE_USDT, SUBSCRIPTION_PERIOD_DAYS, SUBSCRIPTION_ASSET
from core.user_context import require_auth, get_current_user_id
from services.bingx_api import get_balance
from services.auto_sync import sync_trades
from core.scheduler import update_pinned_status
from services.trade_manager import TradeManager
from utils.telegram_text import clean_markdown as _clean, send_long as _send_long, strip_llm_self_correction

CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')  # owner-only команды на переходный период миграции


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Не context.user_data.clear() целиком — это стёрло бы user/is_owner/
    # is_authorized, которые уже установил middleware (core/user_context.py,
    # group=-1, отрабатывает раньше этого хендлера). Чистим только
    # состояние диалога.
    for key in ('state', 'comment_order_id', 'entry_order_id', 'setup_trade_id', 'consilium_positions'):
        context.user_data.pop(key, None)

    db = get_db()
    user = context.user_data.get('user')
    if not user:
        telegram_id = str(update.effective_user.id) if update.effective_user else str(update.effective_chat.id)
        username = update.effective_user.username if update.effective_user else None
        user = db.get_or_create_user(telegram_id, username)

    is_authorized = context.user_data.get('is_authorized', False)
    tier_label = "Premium ⭐️" if db.is_premium(user['user_id']) else "Free"

    if is_authorized:
        text = (
            "👋 *AI Helper Bot*\n\n"
            "Твой помощник трейдера.\n"
            "Отслеживаю сделки, веду дневник, считаю статистику.\n\n"
            "Используй кнопки меню 👇"
        )
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu_keyboard())
    else:
        text = (
            f"👋 *AI Helper Bot*\n\n"
            f"Твой тариф: {tier_label}\n\n"
            f"Пробный период или подписка закончились. Продли доступ: /subscribe "
            f"({SUBSCRIPTION_PRICE_USDT} {SUBSCRIPTION_ASSET} / {SUBSCRIPTION_PERIOD_DAYS} дней).\n\n"
            f"Если ещё не привязал BingX-ключи (только чтение) — сделай это через /setkeys."
        )
        await update.message.reply_text(text, parse_mode='Markdown')


async def show_help(update: Update):
    text = (
        "ℹ️ *Помощь*\n\n"
        "📈 *Trading* — работа со сделками:\n"
        "  • 💰 Баланс — текущий баланс BingX\n"
        "  • 📋 Последние сделки — открытые и закрытые позиции\n"
        "  • 📊 Статистика — Win Rate, PNL и др.\n"
        "  • 🧠 AI-анализ — общий анализ портфеля\n"
        "  • 🧠 Консилиум — AI-анализ позиций и новых сетапов\n\n"
        "🔄 Синхронизация каждые 15 секунд.\n\n"
        "📌 *Команды:*\n"
        "/start — главное меню\n"
        "/setkeys — привязать/обновить BingX API-ключи (только чтение)\n"
        "/subscribe — оплатить/продлить подписку\n"
        "/sync — ручная синхронизация\n"
        "/status — текущий статус (баланс, позиции, правила)\n"
        "/calc — калькулятор позиции\n"
        "/setidea — установить торговую идею и уровни\n"
        "/analyze — AI-анализ позиции (стопы, тейки, решение)\n"
        "/ai\\_fix — AI-разбор серии убыточных сделок\n"
        "/test\\_behavior — тест детекторов поведения\n"
        "/health — состояние систем"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu_keyboard())


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return
    db = get_db()
    ai_analyzer = get_ai_analyzer()
    msg = await update.message.reply_text("🩺 Проверяю здоровье систем...")
    status = []
    try:
        db.get_open_trades()
        status.append("🗄 База данных: 🟢")
    except Exception:
        status.append("🗄 База данных: 🔴")
    try:
        balance = await get_balance()
        if balance.get('success'):
            status.append("📡 BingX API: 🟢")
        else:
            status.append(f"📡 BingX API: 🔴 ({balance.get('error', 'неизвестно')})")
    except Exception as e:
        status.append(f"📡 BingX API: 🔴 ({e})")
    if ai_analyzer.provider:
        try:
            test = ai_analyzer.provider.generate("ping")
            if test and "unavailable" not in test:
                status.append("🧠 AI-провайдер: 🟢")
            else:
                status.append("🧠 AI-провайдер: 🔴 (не отвечает)")
        except Exception as e:
            status.append(f"🧠 AI-провайдер: 🔴 ({e})")
    else:
        status.append("🧠 AI-провайдер: 🔴 (нет ключа)")
    if os.getenv('HTTP_PROXY') or os.getenv('HTTPS_PROXY'):
        status.append("🌐 Прокси: 🟢 (настроен)")
    else:
        status.append("🌐 Прокси: ⚪ (не используется)")
    await msg.edit_text("📊 *Health Check*\n\n" + "\n".join(status), parse_mode='Markdown')


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return
    chat_id = str(update.effective_chat.id)
    user_id = get_current_user_id(context)
    msg = await update.message.reply_text("🔄 Синхронизирую сделки с BingX...")
    results = await sync_trades(context.bot, chat_id, user_id)
    new_open   = len(results.get('new_open', []))
    new_closed = len(results.get('new_closed', []))
    await msg.edit_text(
        f"✅ Синхронизация завершена!\n\n"
        f"🆕 Новых позиций: {new_open}\n"
        f"🔒 Закрыто позиций: {new_closed}"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Пока owner-only (не общий require_auth): закреплённый статус —
    # общее состояние ('bot_state', см. core/scheduler.py), не per-user —
    # открывать его всем подписчикам сейчас означало бы показывать чужой
    # баланс/позиции. Полноценный per-user статус — Этап 6/7 миграции.
    if not context.user_data.get('is_owner'):
        return
    db = get_db()
    await update_pinned_status(context, db, CHAT_ID, force=True)
    await update.message.reply_text(
        "📌 Статус обновлён. Смотри закреплённое сообщение.",
        reply_markup=main_menu_keyboard()
    )


async def ai_fix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return
    db = get_db()
    ai_analyzer = get_ai_analyzer()
    msg = await update.message.reply_text("🤖 Анализирую убыточные сделки...")
    last_trades = db.get_closed_trades(limit=5, user_id=get_current_user_id(context))
    losing = [t for t in last_trades if t['realized_pnl'] < 0]
    if not losing:
        await msg.edit_text("Убыточных сделок не найдено.")
        return
    trades_text = json.dumps(
        [{'symbol': t['symbol'], 'side': t['side'], 'pnl': t['realized_pnl'],
          'comment': t.get('comment', '')} for t in losing],
        ensure_ascii=False, indent=2
    )
    prompt = (
        "Проанализируй убыточные сделки трейдера. Отвечай строго по структуре:\n\n"
        "ОШИБКИ (максимум 3, каждая одним предложением):\n"
        "1. ...\n"
        "2. ...\n"
        "3. ...\n\n"
        "ГЛАВНАЯ ПРИЧИНА (одно предложение):\n"
        "...\n\n"
        "ЧТО СДЕЛАТЬ ПРЯМО СЕЙЧАС (максимум 2 пункта):\n"
        "1. ...\n"
        "2. ...\n\n"
        "Без вступления, без воды, только факты из данных ниже. "
        "Дай сразу финальный вариант — не пиши в ответе черновые мысли, самокоррекции "
        "или пометки вроде «ошибся»/«на самом деле»/«заменю на».\n"
        f"Сделки:\n{trades_text}"
    )
    answer = strip_llm_self_correction(_clean(await ai_analyzer.analyze_raw(prompt, max_tokens=1500)))
    await _send_long(msg, f"🧠 AI-разбор убытков:\n\n{answer}")


async def test_behavior_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return
    from services.database import Database
    from services.behavior_engine import BehaviorEngine, format_alert
    from services.bingx_api import get_kline

    db     = Database()
    engine = BehaviorEngine(db)
    user_id = get_current_user_id(context)

    msg = await update.message.reply_text("🧪 Тестирую детекторы поведения на реальных данных...")
    results = []

    overtrading = engine.detect_overtrading(user_id)
    if overtrading:
        results.append(("Overtrading", format_alert(overtrading)))
    else:
        results.append(("Overtrading", "Не сработал — частота входов в норме"))

    closed     = db.get_closed_trades(limit=10, user_id=user_id)
    panic_hits = []
    for t in closed:
        panic = engine.detect_panic_close(t)
        if panic:
            panic_hits.append(f"{t['symbol']} (PNL ${float(t['realized_pnl']):.2f})")
    if panic_hits:
        results.append(("Panic Close", f"Сработал бы на: {', '.join(panic_hits)}"))
    else:
        results.append(("Panic Close", "Не сработал — нет быстрых закрытий в убыток без стопа"))

    open_trades = db.get_open_trades(user_id=user_id)
    if open_trades:
        candidate = open_trades[0]
        fake_new_trade = {
            'symbol':     candidate['symbol'],
            'entryPrice': candidate['entry_price'],
            'positionAmt': candidate['quantity'],
            'side':       candidate['side']
        }
        revenge = engine.detect_revenge_trading(user_id, fake_new_trade)
        if revenge:
            results.append(("Revenge Trading", format_alert(revenge)))
        else:
            results.append(("Revenge Trading", f"Не сработал на {candidate['symbol']} — нет признаков отыгрыша"))

        kline_result = await get_kline(candidate['symbol'], "1h", 2)
        if kline_result.get('success'):
            fomo = engine.detect_fomo(fake_new_trade, kline_result.get('klines', []))
            if fomo:
                results.append(("FOMO", format_alert(fomo)))
            else:
                results.append(("FOMO", f"Не сработал на {candidate['symbol']} — вход не выглядит погоней"))
        else:
            results.append(("FOMO", "Не удалось получить данные свечей"))
    else:
        results.append(("Revenge Trading", "Нет открытых позиций для проверки"))
        results.append(("FOMO", "Нет открытых позиций для проверки"))

    text = "🧪 Результаты теста Behavior Engine:\n\n"
    for name, result in results:
        text += f"▪️ {name}:\n{result}\n\n"
    await msg.edit_text(text[:4096])


async def calc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return

    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "Использование:\n"
            "/calc СИМВОЛ ЦЕНА ПЛЕЧО [long/short] [риск%] [cross/isolated]\n\n"
            "Примеры:\n"
            "/calc BTC 108000 10x\n"
            "/calc SOL 71.5 20x long 2 cross\n"
            "/calc ETH 3500 5x short 1.5 isolated"
        )
        return

    from services.calc_engine import calculate_position, format_calc_result

    symbol = args[0].upper()
    if '-' not in symbol:
        symbol = f"{symbol}-USDT"

    try:
        price = float(args[1].replace(',', '.'))
    except ValueError:
        await update.message.reply_text("❌ Некорректная цена. Пример: /calc BTC 108000 10x")
        return

    try:
        leverage = int(args[2].lower().replace('x', '').replace('х', ''))
    except ValueError:
        await update.message.reply_text("❌ Некорректное плечо. Пример: 10x или 10")
        return

    side         = None
    risk_percent = 1.0
    margin_type  = 'isolated'

    for arg in args[3:]:
        arg_lower = arg.lower()
        if arg_lower in ('long', 'short', 'лонг', 'шорт'):
            side = 'LONG' if arg_lower in ('long', 'лонг') else 'SHORT'
        elif arg_lower in ('cross', 'кросс'):
            margin_type = 'cross'
        elif arg_lower in ('isolated', 'изолированная', 'изол'):
            margin_type = 'isolated'
        else:
            try:
                risk_percent = float(arg.replace(',', '.'))
            except ValueError:
                pass

    msg = await update.message.reply_text("⏳ Получаю баланс...")
    balance_result = await get_balance()
    balance = balance_result['equity'] if balance_result.get('success') else 1000.0

    result = calculate_position(symbol, price, leverage, balance, risk_percent, margin_type)
    text   = format_calc_result(result, side)
    await msg.edit_text(text)


async def setidea_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Использование:\n"
            "/setidea СИМВОЛ \"Идея\" [invalidation_price] [tp1,tp2,...]\n\n"
            "Примеры:\n"
            "/setidea BTC \"bullish continuation from support\" 59500\n"
            "/setidea ETH \"breakout retest\" 3450 3600,3700\n\n"
            "• Символ без USDT (BTC, ETH, SOL и т.д.)\n"
            "• Идея в кавычках\n"
            "• Invalidation — цена слома идеи\n"
            "• TP-зоны через запятую (опционально)"
        )
        return

    db = get_db()
    tm = TradeManager(db)

    symbol = args[0].upper()
    if '-' not in symbol:
        symbol = f"{symbol}-USDT"

    # user_id=... — иначе искали бы позицию среди чужих сделок (см.
    # MULTITENANCY_MIGRATION_PLAN.md, "разграничение данных").
    open_positions = db.get_open_trades(user_id=get_current_user_id(context))
    target_order_id = None
    for pos in open_positions:
        if pos['symbol'].upper() == symbol:
            target_order_id = pos['orderId']
            break

    if not target_order_id:
        await update.message.reply_text(f"❌ Нет открытой позиции по {symbol}")
        return

    idea = None
    invalidation_sl = None
    tp_zones = []

    raw_tail = " ".join(args[1:])

    idea_match = re.search(r'"([^"]*)"', raw_tail)
    if idea_match:
        idea = idea_match.group(1)
        raw_tail = raw_tail.replace(f'"{idea}"', '').strip()

    tokens = raw_tail.split()
    for tok in tokens:
        if ',' in tok:
            try:
                parts = tok.split(',')
                for p in parts:
                    tp_zones.append(float(p.strip()))
            except ValueError:
                pass
        else:
            try:
                num = float(tok)
                if invalidation_sl is None:
                    invalidation_sl = num
                else:
                    tp_zones.append(num)
            except ValueError:
                pass

    if idea is None:
        await update.message.reply_text("❌ Не указана идея. Заключите её в кавычки. Пример: /setidea BTC \"поддержка\" 59500")
        return

    tm.set_idea(target_order_id, idea, invalidation_sl, tp_zones if tp_zones else None)

    response = f"✅ Идея для {symbol} установлена:\n🎯 {idea}"
    if invalidation_sl:
        response += f"\n🛑 Invalidation SL: ${invalidation_sl:.4f}"
    if tp_zones:
        zones_str = ', '.join([f"${z:.4f}" for z in tp_zones])
        response += f"\n🎯 TP Zones: {zones_str}"

    await update.message.reply_text(response)


# ─────────────────────────── /analyze ───────────────────────────
async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        return

    args = context.args
    db = get_db()
    user_id = get_current_user_id(context)

    if args:
        symbol = args[0].upper()
        if '-' not in symbol:
            symbol = f"{symbol}-USDT"
        open_positions = db.get_open_trades(user_id=user_id)
        target = None
        for pos in open_positions:
            if pos['symbol'].upper() == symbol:
                target = pos
                break
        if not target:
            await update.message.reply_text(f"❌ Нет открытой позиции по {symbol}")
            return
    else:
        open_positions = db.get_open_trades(user_id=user_id)
        if not open_positions:
            await update.message.reply_text("❌ Нет открытых позиций для анализа")
            return
        target = open_positions[0]

    msg = await update.message.reply_text("🔍 Анализирую позицию...")

    from services.market_data import get_market_snapshot
    from services.ai_decision_engine import analyze_decision

    snapshot = await get_market_snapshot(target['symbol'])
    decision = analyze_decision(snapshot, target)

    side = target.get('side', 'LONG')
    emoji = "🟢" if side == 'LONG' else "🔴"
    pnl = float(target.get('unrealized_pnl', 0))
    pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"

    text = (
        f"{emoji} *{target['symbol']} {side}* | PnL: ${pnl:+.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    idea = target.get('idea')
    if idea:
        text += f"🎯 *Идея:* {idea}\n"

    struct = decision['details'].get('structure', {})
    trend = struct.get('trend', 'UNKNOWN')
    trend_emoji = {'BULLISH': '📈', 'BEARISH': '📉', 'RANGING': '📊'}.get(trend, '❓')
    text += f"{trend_emoji} *Тренд:* {trend}\n"

    dca_count = int(target.get('dca_count', 0))
    text += f"📐 *DCA:* {dca_count}/2\n\n"

    stop = decision['details'].get('stop', {})
    if stop.get('hard_sl'):
        text += f"🛑 *Hard SL:* ${stop['hard_sl']:.4f}"
        if stop.get('status') == 'exit':
            text += " ⚠️ ДОСТИГНУТ!"
        text += "\n"
    if stop.get('recommended_sl'):
        text += f"🔒 *Recommended SL:* ${stop['recommended_sl']:.4f} ({stop.get('reason', '')})\n"
    text += "\n"

    tp = decision['details'].get('tp', {})
    if tp.get('tp1'):
        text += f"🎯 *TP1:* ${tp['tp1']:.4f}"
        if tp.get('status') == 'tp1_near':
            text += " ← БЛИЗКО"
        text += "\n"
    if tp.get('tp2'):
        text += f"🎯 *TP2:* ${tp['tp2']:.4f}"
        if tp.get('status') == 'tp2_near':
            text += " ← БЛИЗКО"
        text += "\n"
    if tp.get('runner'):
        text += f"🏃 *Runner:* ${tp['runner']:.4f}\n"
    text += "\n"

    dec = decision.get('decision', 'UNKNOWN')
    conf = decision.get('confidence', 'low')
    dec_emoji = {
        'HOLD': '✋',
        'EXIT': '🚪',
        'DCA': '📥',
        'PARTIAL_TP': '💰',
        'FULL_TP': '🏆'
    }.get(dec, '❓')
    conf_label = {'high': 'Высокая', 'medium': 'Средняя', 'low': 'Низкая'}.get(conf, conf)
    text += f"{dec_emoji} *Решение:* {dec}\n"
    text += f"📊 *Уверенность:* {conf_label}\n"
    text += f"💬 *Причина:* {decision.get('reason', '—')}"

    await msg.edit_text(text, parse_mode='Markdown')


async def debug_positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ВРЕМЕННАЯ diagnostic-команда (см. AUDIT.md, "SOL-USDT — TP/SL с
    BingX не подтягивается несмотря на фикс One-Way Mode") — сырой,
    необработанный ответ /user/positions и /trade/openOrders прямо в
    Telegram, без прохождения через get_open_positions()/маппинг SL/TP.
    Нужна, чтобы увидеть реальные raw-поля (type/positionSide/closePosition)
    вместо догадок по документации — два предыдущих фикса были основаны на
    документации/community-репортах, не на реальном ответе для этого
    аккаунта. Убрать команду после того, как кейс будет закрыт.

    Owner-only (не require_auth) — дампит сырые ответы биржи, не должна
    быть доступна произвольным подписчикам."""
    if not context.user_data.get('is_owner'):
        return
    from services.bingx_api import _request_with_retry

    msg = await update.message.reply_text("🔍 Забираю сырой ответ BingX...")

    positions_raw = await _request_with_retry('GET', '/openApi/swap/v2/user/positions')
    orders_raw = await _request_with_retry('GET', '/openApi/swap/v2/trade/openOrders')

    text = (
        "🔍 RAW /user/positions:\n"
        f"{json.dumps(positions_raw, ensure_ascii=False, indent=2)}\n\n"
        "🔍 RAW /trade/openOrders:\n"
        f"{json.dumps(orders_raw, ensure_ascii=False, indent=2)}"
    )
    await _send_long(msg, text)