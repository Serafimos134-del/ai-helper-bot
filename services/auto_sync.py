"""
services/auto_sync.py
Refactored sync engine with global lock, atomic trade closing, error resilience,
and Behavior Alerts Engine hooks.
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from services.bingx_api import get_open_positions, get_kline
from services.database import Database
from services.behavior_engine import BehaviorEngine, format_alert
from ai.trade_scorer import TradeScorer
from core.container import get_orchestrator
from utils.formatting import format_verdict, format_score_breakdown
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

db = Database()
trade_scorer = TradeScorer()
behavior_engine = BehaviorEngine(db)

_sync_lock = asyncio.Lock()
_missing_cycles: dict = {}
_missing_cycles_lock = asyncio.Lock()
_MISSING_CYCLES_CATEGORY = 'missing_cycles'


def _load_missing_cycles() -> dict:
    try:
        raw = db.memory_get_all(_MISSING_CYCLES_CATEGORY)
        return {k: int(v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_missing_cycle(oid: str, count: int):
    try:
        db.memory_set(_MISSING_CYCLES_CATEGORY, oid, str(count))
    except Exception as e:
        logger.warning(f"Не удалось сохранить missing_cycle для {oid}: {e}")


def _delete_missing_cycle(oid: str):
    try:
        db._execute(
            "DELETE FROM trader_memory WHERE category = ? AND key = ?",
            (_MISSING_CYCLES_CATEGORY, oid)
        )
        db._commit()
    except Exception as e:
        logger.warning(f"Не удалось удалить missing_cycle для {oid}: {e}")


_missing_cycles = _load_missing_cycles()
_MISSING_THRESHOLD = 2


def _calculate_exit_price(trade: dict) -> float:
    """ОЦЕНКА цены выхода, а не фактическая цена исполнения.
    BingX positions API не отдаёт реальную цену закрытия — здесь мы
    восстанавливаем её из последнего известного unrealized_pnl перед тем, как
    позиция исчезла из ответа API. Это приближение (не учитывает комиссии/
    funding), поэтому в уведомлениях помечаем как оценочное значение."""
    entry = float(trade.get('entry_price', 0))
    qty   = float(trade.get('quantity', 0))
    pnl   = float(trade.get('unrealized_pnl', 0))
    side  = trade.get('side', 'LONG')
    if qty == 0:
        return entry
    return entry + (pnl / qty) if side == 'LONG' else entry - (pnl / qty)


async def _check_behavior_on_open(bot, chat_id: str, user_id: str, trade: dict):
    """Запускает Behavior Engine при открытии новой позиции."""
    try:
        revenge = behavior_engine.detect_revenge_trading(user_id, trade)
        if revenge:
            behavior_engine.save_event(user_id, revenge)
            await bot.send_message(chat_id=chat_id, text=format_alert(revenge))

        overtrading = behavior_engine.detect_overtrading(user_id)
        if overtrading:
            behavior_engine.save_event(user_id, overtrading)
            await bot.send_message(chat_id=chat_id, text=format_alert(overtrading))

        symbol = trade.get('symbol')
        if symbol:
            kline_result = await get_kline(symbol, "1h", 2)
            if kline_result.get('success'):
                fomo = behavior_engine.detect_fomo(trade, kline_result.get('klines', []))
                if fomo:
                    behavior_engine.save_event(user_id, fomo)
                    await bot.send_message(chat_id=chat_id, text=format_alert(fomo))
    except Exception as e:
        logger.error(f"Ошибка Behavior Engine (open): {e}")


async def _check_behavior_on_close(bot, chat_id: str, user_id: str, closed_trade: dict):
    """Запускает Behavior Engine при закрытии позиции."""
    try:
        panic = behavior_engine.detect_panic_close(closed_trade)
        if panic:
            behavior_engine.save_event(user_id, panic)
            await bot.send_message(chat_id=chat_id, text=format_alert(panic))
    except Exception as e:
        logger.error(f"Ошибка Behavior Engine (close): {e}")


async def _analyze_new_trade(trade: dict):
    """Trader Memory (Этап 8): сохраняет AI-разбор позиции в момент открытия.
    Не отправляет уведомление — /consilium (Этап 4) уже даёт разбор по
    запросу, здесь только персистентность для будущего Trader DNA (Этап 9)."""
    order_id = trade.get('orderId')
    if not order_id:
        return
    try:
        orchestrator = get_orchestrator()
        analysis = await orchestrator.review_open_position(trade)
        payload = {
            'market_review':    analysis.get('market_review'),
            'risk_review':      analysis.get('risk_review'),
            'psychology_review': analysis.get('psychology_review'),
            'judge_verdict':    analysis.get('judge_verdict'),
            'position_plan':    analysis.get('position_plan'),
        }
        await asyncio.to_thread(db.add_trade_event, str(order_id), 'open_analysis', json.dumps(payload, ensure_ascii=False))
        logger.info(f"Trader Memory: сохранён анализ открытия для {order_id}")
    except Exception as e:
        logger.error(f"Ошибка анализа открытия позиции {order_id}: {e}")


async def _analyze_and_notify(bot, chat_id: str, trade_id: int, closed_trade: dict, stored: dict):
    try:
        orchestrator = get_orchestrator()
        analysis = await orchestrator.review_closed_trade(closed_trade)
        # score_breakdown теперь считает сам AIOrchestrator (единый источник —
        # раньше auto_sync.py и core/router.py независимо решали, вызывать
        # TradeScorer или нет, и router.py вообще не сохранял оценку).
        score = analysis['score_breakdown']

        await asyncio.to_thread(db.update_trade_metrics, trade_id,
                                ai_score=score['total_score'],
                                score_breakdown=json.dumps(score, ensure_ascii=False),
                                market_review=analysis['market_review'],
                                risk_review=analysis['risk_review'],
                                psychology_review=analysis['psychology_review'],
                                judge_verdict=analysis['judge_verdict'],
                                market_trend=analysis.get('market_trend'))
        logger.info(f"Сделка #{trade_id} проанализирована")

        # MemoryEngine.update() (инкрементальные счётчики в trader_memory)
        # убран при консолидации в TraderContext (Этап 5, см.
        # TRADER_INTELLIGENCE_ARCHITECTURE.md, §8) — дублировал
        # PerformanceEngine, который TraderContext уже использует напрямую
        # из closed_trades, всегда пересчитывая точно, без риска рассинхрона.

        # Trader Memory (Этап 8): closed_trades уже хранит финальный снимок
        # анализа закрытия (market_review/risk_review/...), но здесь ещё и
        # добавляем его в общий хронологический журнал сделки (order_id) —
        # чтобы Trader DNA (Этап 9) мог читать last open_analysis -> N
        # companion -> close_analysis одним запросом, а не собирать их из
        # разных таблиц.
        order_id = closed_trade.get('orderId')
        if order_id:
            try:
                payload = {
                    'market_review':    analysis.get('market_review'),
                    'risk_review':      analysis.get('risk_review'),
                    'psychology_review': analysis.get('psychology_review'),
                    'judge_verdict':    analysis.get('judge_verdict'),
                    'score_breakdown':  score,
                }
                await asyncio.to_thread(db.add_trade_event, str(order_id), 'close_analysis',
                                         json.dumps(payload, ensure_ascii=False))
            except Exception as event_e:
                logger.error(f"Ошибка записи Trader Memory для сделки #{trade_id}: {event_e}")

        try:
            verdict_line = format_verdict(analysis.get('judge_verdict', '{}'))
            text = (
                f"🧠 AI-разбор сделки #{trade_id}\n\n"
                f"📈 Рынок:\n{analysis.get('market_review', '—')}\n\n"
                f"⚠️ Риск:\n{analysis.get('risk_review', '—')}\n\n"
                f"🧘 Психология:\n{analysis.get('psychology_review', '—')}\n\n"
                f"⚖️ Вердикт: {verdict_line}\n\n"
                f"{format_score_breakdown(score)}"
            )
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as notify_e:
            logger.error(f"Ошибка отправки AI-разбора для сделки #{trade_id}: {notify_e}")

    except Exception as e:
        logger.error(f"Ошибка фонового анализа сделки #{trade_id}: {e}")
        try:
            score = trade_scorer.score(closed_trade)
            await asyncio.to_thread(db.update_trade_metrics, trade_id, ai_score=score['total_score'])
        except Exception as fallback_e:
            logger.error(f"Ошибка fallback-оценки для сделки #{trade_id}: {fallback_e}")


async def sync_trades(bot, chat_id: str) -> dict:
    if _sync_lock.locked():
        logger.debug("Синхронизация пропущена (уже выполняется)")
        return {'new_open': [], 'new_closed': []}
    async with _sync_lock:
        return await _sync_trades_impl(bot, chat_id)


async def _sync_trades_impl(bot, chat_id: str) -> dict:
    global _missing_cycles
    user_id = 'default'
    # api_ok сообщает вызывающему коду (core/scheduler.py), удался ли реальный
    # запрос к BingX — используется для backoff при затяжных сбоях API.
    results = {'new_open': [], 'new_closed': [], 'api_ok': True}
    logger.info("=== Синхронизация начата ===")

    try:
        open_result = await get_open_positions()
    except Exception as e:
        logger.error(f"Ошибка вызова API позиций: {e}")
        results['api_ok'] = False
        return results

    if not open_result.get('success'):
        logger.warning(f"Ошибка получения открытых позиций: {open_result.get('error')}")
        results['api_ok'] = False
        return results

    api_trades = [t for t in open_result.get('trades', []) if t.get('orderId')]
    api_ids = {str(t['orderId']) for t in api_trades}

    stored_open = await asyncio.to_thread(db.get_open_trades, user_id)
    stored_by_id = {}
    for t in stored_open:
        oid = str(t.get('orderId')) if t.get('orderId') else None
        if oid:
            stored_by_id[oid] = t

    new_trades_for_behavior_check = []

    async with _missing_cycles_lock:
        for trade in api_trades:
            oid  = str(trade['orderId'])
            side = trade.get('side', '')

            if oid in _missing_cycles:
                _missing_cycles.pop(oid, None)
                _delete_missing_cycle(oid)

            if oid in stored_by_id:
                try:
                    await asyncio.to_thread(db.update_open_trade_by_order_id,
                        oid,
                        unrealized_pnl=float(trade.get('unrealizedPnl', 0)),
                        leverage=float(trade.get('leverage', 1)),
                        quantity=abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                        entry_price=float(trade.get('entryPrice', 0)),
                        stop_loss=trade.get('stopLoss'),
                        take_profit=trade.get('takeProfit')
                    )
                except Exception as e:
                    logger.error(f"Ошибка обновления позиции {oid}: {e}")
                stored_by_id.pop(oid)
            else:
                try:
                    await asyncio.to_thread(db.add_open_trade, {
                        'user_id':        user_id,
                        'orderId':        trade['orderId'],
                        'symbol':         trade.get('symbol'),
                        'side':           side,
                        'entry_price':    float(trade.get('entryPrice', 0)),
                        'quantity':       abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                        'leverage':       float(trade.get('leverage', 1)),
                        'unrealized_pnl': float(trade.get('unrealizedPnl', 0)),
                        'stop_loss':      trade.get('stopLoss'),
                        'take_profit':    trade.get('takeProfit'),
                        'entry_comment':  ''
                    })
                    results['new_open'].append(trade)
                    new_trades_for_behavior_check.append(trade)
                    await _notify_new_trade(bot, chat_id, trade)
                except sqlite3.IntegrityError:
                    logger.warning(f"Позиция {oid} уже существует в БД, пропущена")
                except Exception as e:
                    logger.error(f"Ошибка добавления позиции {oid}: {e}")

        truly_closed = {}
        for oid, stored in list(stored_by_id.items()):
            if oid in api_ids:
                continue
            cycles = _missing_cycles.get(oid, 0) + 1
            _missing_cycles[oid] = cycles
            _save_missing_cycle(oid, cycles)
            if cycles >= _MISSING_THRESHOLD:
                _missing_cycles.pop(oid, None)
                _delete_missing_cycle(oid)
                truly_closed[oid] = stored
            else:
                logger.info(f"Позиция {oid} отсутствует {cycles}/{_MISSING_THRESHOLD} циклов — ждём")

        for oid in list(_missing_cycles.keys()):
            if oid not in stored_by_id:
                _missing_cycles.pop(oid, None)
                _delete_missing_cycle(oid)

    # Behavior Engine: проверка новых позиций (после release лока, чтобы не блокировать sync)
    for trade in new_trades_for_behavior_check:
        asyncio.create_task(_check_behavior_on_open(bot, chat_id, user_id, trade))
        # Trader Memory (Этап 8): анализ открытия раньше нигде не сохранялся —
        # AI-разбор позиции существовал только "по запросу" через /consilium
        # и не оставлял следа в БД. Фоново, чтобы не блокировать sync.
        asyncio.create_task(_analyze_new_trade(trade))

    for oid, stored in truly_closed.items():
        closed_trade = _build_closed_trade(stored, user_id)
        try:
            new_id = await asyncio.to_thread(db.close_trade_atomic, oid, closed_trade)
            results['new_closed'].append(stored)
            await _notify_closed_trade(bot, chat_id, closed_trade, closed_trade['realized_pnl'], new_id)
            asyncio.create_task(_check_behavior_on_close(bot, chat_id, user_id, closed_trade))
            asyncio.create_task(_analyze_and_notify(bot, chat_id, new_id, closed_trade, stored))
        except sqlite3.IntegrityError:
            logger.warning(f"Закрытие {oid}: дубликат в closed_trades, принудительно удаляю из open_trades")
            await asyncio.to_thread(db.delete_open_trade_by_order_id, oid)
        except ValueError as e:
            logger.error(f"Закрытие {oid}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка при закрытии {oid}: {e}")

    logger.info(f"=== Синхронизация завершена: открыто {len(results['new_open'])}, закрыто {len(results['new_closed'])} ===")
    return results


def _build_closed_trade(stored_open: dict, user_id: str = 'default') -> dict:
    now = datetime.now(timezone.utc)
    open_time = stored_open.get('created_at')
    close_time = now.isoformat()
    holding_minutes = None

    if open_time:
        try:
            open_dt = datetime.fromisoformat(open_time) if isinstance(open_time, str) else open_time
            if open_dt.tzinfo is None:
                open_dt = open_dt.replace(tzinfo=timezone.utc)
            holding_minutes = int((now - open_dt).total_seconds() / 60)
        except Exception as e:
            logger.warning(f"Не удалось вычислить holding_minutes: {e}")

    exit_price = _calculate_exit_price(stored_open)

    return {
        'user_id':       stored_open.get('user_id', user_id),
        'orderId':       stored_open['orderId'],
        'symbol':        stored_open['symbol'],
        'side':          stored_open['side'],
        'entry_price':   float(stored_open.get('entry_price', 0)),
        'exit_price':    exit_price,
        'quantity':      float(stored_open.get('quantity', 0)),
        'realized_pnl':  float(stored_open.get('unrealized_pnl', 0)),
        'comment':       '',
        'risk_percent':  0,
        'leverage':      float(stored_open.get('leverage', 1)),
        'stop_loss':     stored_open.get('stop_loss'),
        'take_profit':   stored_open.get('take_profit'),
        'risk_reward':   None,
        'open_time':     open_time,
        'close_time':    close_time,
        'entry_comment': stored_open.get('entry_comment', ''),
        'exit_comment':  '',
        'ai_review':     '',
        'holding_minutes': holding_minutes,
        'btc_price':     None,
        'eth_price':     None,
        'market_trend':  None,
        'setup_type':    None,
        'mistakes':      None,
        'ai_score':      None
    }


async def _notify_new_trade(bot, chat_id: str, trade: dict):
    try:
        symbol     = trade.get('symbol', '?')
        side       = trade.get('side', '')
        entry      = float(trade.get('entryPrice', 0))
        size       = abs(float(trade.get('positionAmt', trade.get('size', 0))))
        leverage   = trade.get('leverage', 1)
        sl         = trade.get('stopLoss')
        tp         = trade.get('takeProfit')
        side_emoji = "🟢" if side == 'LONG' else "🔴"
        sl_line    = f"🛑 Стоп: ${float(sl):.4f}\n" if sl else ""
        tp_line    = f"🎯 Тейк: ${float(tp):.4f}\n" if tp else ""

        text = (
            f"🔔 *Новая позиция открыта!*\n\n"
            f"{side_emoji} {symbol} — {side}\n"
            f"💵 Цена входа: ${entry:.4f}\n"
            f"📦 Размер: {size}\n"
            f"⚡️ Плечо: {leverage}x\n"
            f"{sl_line}{tp_line}\n"
            f"*Напишите причину входа или нажмите «Пропустить»:*"
        )
        callback_id = trade.get('orderId') or 'no-id'
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Комментарий", callback_data=f"entry_reason_{callback_id}"),
             InlineKeyboardButton("⏭ Пропустить",   callback_data="skip_entry_reason")]
        ])
        await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка уведомления об открытии: {e}")


async def _notify_closed_trade(bot, chat_id: str, trade: dict, pnl: float, trade_id: int = None):
    try:
        symbol     = trade.get('symbol', '?')
        side       = trade.get('side', '?')
        pnl_emoji  = "✅" if pnl >= 0 else "❌"
        exit_price = trade.get('exit_price', 0)
        holding    = trade.get('holding_minutes')
        sl         = trade.get('stop_loss')
        tp         = trade.get('take_profit')

        sl_line = f"🛑 Стоп: ${float(sl):.4f}\n" if sl else ""
        tp_line = f"🎯 Тейк: ${float(tp):.4f}\n" if tp else ""

        if holding is not None:
            h, m = divmod(holding, 60)
            duration_line = f"⏱ Длительность: {h}ч {m}мин\n" if h else f"⏱ Длительность: {m} мин\n"
        else:
            duration_line = ""

        text = (
            f"🔔 *Позиция закрыта!*\n\n"
            f"{pnl_emoji} {symbol} — {side}\n"
            f"💰 PNL: ${pnl:+.2f}\n"
            f"💵 Цена выхода (оценочно): ≈${exit_price:.4f}\n"
            f"{sl_line}{tp_line}{duration_line}\n"
            f"_Цена выхода и PNL восстановлены расчётным путём — биржа не отдаёт точную цену исполнения через этот метод._\n\n"
            f"*Добавьте вывод или выберите сетап:*"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Добавить вывод", callback_data=f"exit_reason_{trade_id}"),
             InlineKeyboardButton("📊 Сетап",          callback_data=f"setup_{trade_id}")],
            [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_comment")]
        ])

        for attempt in range(3):
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=keyboard)
                return
            except Exception as e:
                err_str = str(e).lower()
                if ('retry' in err_str or '429' in err_str or 'flood' in err_str) and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise
    except Exception as e:
        logger.error(f"Ошибка уведомления о закрытии: {e}")