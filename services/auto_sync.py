"""
services/auto_sync.py
Refactored sync engine with global lock, atomic trade closing, and error resilience.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from services.bingx_api import get_open_positions
from services.database import Database
from ai.trade_scorer import TradeScorer
from ai.consensus_engine import ConsensusEngine
from services.ai_trading import AITradingAnalyzer
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

db = Database()
trade_scorer = TradeScorer()

# ─── Глобальная блокировка синхронизации ───
_sync_lock = asyncio.Lock()

# ─── Защита от ложных закрытий ───
_missing_cycles: dict = {}
_missing_cycles_lock = asyncio.Lock()
_MISSING_CYCLES_CATEGORY = 'missing_cycles'


def _load_missing_cycles() -> dict:
    """Загружает счётчики пропусков из БД при старте (переживают рестарт)."""
    try:
        raw = db.memory_get_all(_MISSING_CYCLES_CATEGORY)
        return {k: int(v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_missing_cycle(oid: str, count: int):
    """Сохраняет счётчик пропуска для позиции в БД."""
    try:
        db.memory_set(_MISSING_CYCLES_CATEGORY, oid, str(count))
    except Exception as e:
        logger.warning(f"Не удалось сохранить missing_cycle для {oid}: {e}")


def _delete_missing_cycle(oid: str):
    """Удаляет счётчик пропуска из БД."""
    try:
        db._execute(
            "DELETE FROM trader_memory WHERE category = ? AND key = ?",
            (_MISSING_CYCLES_CATEGORY, oid)
        )
        db._commit()
    except Exception as e:
        logger.warning(f"Не удалось удалить missing_cycle для {oid}: {e}")


# Загружаем счётчики при старте — переживают рестарт бота
_missing_cycles = _load_missing_cycles()
_MISSING_THRESHOLD = 2   # закрываем только после 2 пропусков (30+ сек)


def _calculate_exit_price(trade: dict) -> float:
    """Вычисляет реальную цену выхода на основе PnL и размера позиции."""
    entry = float(trade.get('entry_price', 0))
    qty = float(trade.get('quantity', 0))
    pnl = float(trade.get('unrealized_pnl', 0))
    side = trade.get('side', 'LONG')
    if qty == 0:
        return entry
    if side == 'LONG':
        return entry + (pnl / qty)
    else:
        return entry - (pnl / qty)


async def _analyze_and_notify(bot, chat_id: str, trade_id: int, closed_trade: dict, stored: dict):
    """
    Фоновый анализ закрытой сделки: Consensus Engine + Trade Scorer.
    Выполняется асинхронно, не блокирует основной sync loop.
    """
    from ai.memory_engine import MemoryEngine

    try:
        ai_provider = AITradingAnalyzer().provider
        engine = ConsensusEngine(ai_provider)
        analysis = await engine.analyze_closed_trade(closed_trade)
        score = trade_scorer.score(closed_trade)
        await asyncio.to_thread(db.update_trade_metrics, trade_id,
                                ai_score=score['total_score'],
                                market_review=analysis['market_review'],
                                risk_review=analysis['risk_review'],
                                psychology_review=analysis['psychology_review'],
                                judge_verdict=analysis['judge_verdict'])
        logger.info(f"Сделка #{trade_id} проанализирована консилиумом: {analysis['judge_verdict']}")

        try:
            mem = MemoryEngine()
            await mem.update(closed_trade)
            logger.info(f"Memory Engine обновлён для сделки #{trade_id}")
        except Exception as mem_e:
            logger.error(f"Ошибка обновления Memory Engine для сделки #{trade_id}: {mem_e}")

        try:
            symbol = stored.get('symbol', '?')
            side = stored.get('side', '?')
            text = (
                f"🧠 *AI-разбор сделки #{trade_id}*\n\n"
                f"📈 Рынок: {analysis.get('market_review', '—')}\n\n"
                f"⚠️ Риск: {analysis.get('risk_review', '—')}\n\n"
                f"🧘 Психология: {analysis.get('psychology_review', '—')}\n\n"
                f"⚖️ Вердикт: {analysis.get('judge_verdict', '—')}"
            )
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as notify_e:
            logger.error(f"Ошибка отправки AI-разбора для сделки #{trade_id}: {notify_e}")

    except Exception as e:
        logger.error(f"Ошибка фонового анализа сделки #{trade_id}: {e}")
        try:
            score = trade_scorer.score(closed_trade)
            await asyncio.to_thread(db.update_trade_metrics, trade_id, ai_score=score['total_score'])
            logger.info(f"Сделка #{trade_id} оценена (fallback): {score['total_score']}/10")
        except Exception as fallback_e:
            logger.error(f"Ошибка даже fallback-оценки для сделки #{trade_id}: {fallback_e}")


async def sync_trades(bot, chat_id: str) -> dict:
    """
    Основной цикл синхронизации. Защищён глобальной блокировкой,
    чтобы исключить параллельное выполнение.
    """
    if _sync_lock.locked():
        logger.debug("Синхронизация пропущена (уже выполняется)")
        return {'new_open': [], 'new_closed': []}

    async with _sync_lock:
        return await _sync_trades_impl(bot, chat_id)


async def _sync_trades_impl(bot, chat_id: str) -> dict:
    global _missing_cycles
    results = {'new_open': [], 'new_closed': []}
    logger.info("=== Синхронизация начата ===")

    try:
        open_result = await get_open_positions()
    except Exception as e:
        logger.error(f"Ошибка вызова API позиций: {e}")
        return results

    if not open_result.get('success'):
        logger.warning(f"Ошибка получения открытых позиций: {open_result.get('error')}")
        return results

    api_trades = open_result.get('trades', [])

    valid_api_trades = []
    for t in api_trades:
        oid = t.get('orderId')
        if not oid:
            logger.warning(f"Пропущена позиция без orderId: {t.get('symbol', '?')}")
            continue
        valid_api_trades.append(t)
    api_trades = valid_api_trades

    api_ids = {str(t.get('orderId')) for t in api_trades}

    stored_open = await asyncio.to_thread(db.get_open_trades)
    stored_by_id = {}
    for t in stored_open:
        oid = str(t.get('orderId')) if t.get('orderId') else None
        if oid:
            stored_by_id[oid] = t

    async with _missing_cycles_lock:
        for trade in api_trades:
            oid = str(trade.get('orderId'))
            raw_side = trade.get('side', '')
            side = 'LONG' if raw_side in ('BUY', 'LONG') else 'SHORT'

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
                        'orderId': trade.get('orderId'),
                        'symbol': trade.get('symbol'),
                        'side': side,
                        'entry_price': float(trade.get('entryPrice', 0)),
                        'quantity': abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                        'leverage': float(trade.get('leverage', 1)),
                        'unrealized_pnl': float(trade.get('unrealizedPnl', 0)),
                        'stop_loss': trade.get('stopLoss'),
                        'take_profit': trade.get('takeProfit'),
                        'entry_comment': ''
                    })
                    results['new_open'].append(trade)
                    await _notify_new_trade(bot, chat_id, trade)
                except sqlite3.IntegrityError:
                    logger.warning(f"Новая позиция {oid} уже существует в БД (дубликат), пропущена")
                except Exception as e:
                    logger.error(f"Ошибка добавления новой позиции {oid}: {e}")

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
                logger.info(f"Позиция {oid} ({stored.get('symbol')}) отсутствует {cycles}/{_MISSING_THRESHOLD} циклов — ждём подтверждения")

        for oid in list(_missing_cycles.keys()):
            if oid not in stored_by_id:
                _missing_cycles.pop(oid, None)
                _delete_missing_cycle(oid)

    for oid, stored in truly_closed.items():
        closed_trade = _build_closed_trade(stored)
        try:
            new_id = await asyncio.to_thread(db.close_trade_atomic, oid, closed_trade)
            results['new_closed'].append(stored)
            await _notify_closed_trade(bot, chat_id, closed_trade, closed_trade['realized_pnl'], new_id)
            asyncio.create_task(_analyze_and_notify(bot, chat_id, new_id, closed_trade, stored))
        except sqlite3.IntegrityError:
            logger.warning(f"Закрытие сделки {oid}: дубликат в closed_trades (возможно уже закрыта)")
        except ValueError as e:
            logger.error(f"Закрытие сделки {oid}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка при закрытии сделки {oid}: {e}")

    logger.info(f"=== Синхронизация завершена: новых позиций {len(results['new_open'])}, закрыто {len(results['new_closed'])} ===")
    return results


def _build_closed_trade(stored_open: dict) -> dict:
    """Формирует данные для вставки в closed_trades."""
    now = datetime.now(timezone.utc)
    open_time = stored_open.get('created_at')
    close_time = now.isoformat()
    holding_minutes = None
    if open_time:
        try:
            if isinstance(open_time, str):
                open_dt = datetime.fromisoformat(open_time)
            else:
                open_dt = open_time
            holding_minutes = int((now - open_dt).total_seconds() / 60)
        except Exception:
            pass

    exit_price = _calculate_exit_price(stored_open)

    return {
        'orderId': stored_open['orderId'],
        'symbol': stored_open['symbol'],
        'side': stored_open['side'],
        'entry_price': float(stored_open.get('entry_price', 0)),
        'exit_price': exit_price,
        'quantity': float(stored_open.get('quantity', 0)),
        'realized_pnl': float(stored_open.get('unrealized_pnl', 0)),
        'comment': '',
        'risk_percent': 0,
        'leverage': float(stored_open.get('leverage', 1)),
        'stop_loss': stored_open.get('stop_loss'),
        'take_profit': stored_open.get('take_profit'),
        'risk_reward': None,
        'open_time': open_time,
        'close_time': close_time,
        'entry_comment': stored_open.get('entry_comment', ''),
        'exit_comment': '',
        'ai_review': '',
        'holding_minutes': holding_minutes,
        'btc_price': None,
        'eth_price': None,
        'market_trend': None,
        'setup_type': None,
        'mistakes': None,
        'ai_score': None
    }


# ─── Уведомления ───

async def _notify_new_trade(bot, chat_id: str, trade: dict):
    try:
        symbol = trade.get('symbol', '?')
        raw_side = trade.get('side', '')
        side = 'LONG' if raw_side in ('BUY', 'LONG') else 'SHORT'
        entry = float(trade.get('entryPrice', 0))
        size = abs(float(trade.get('positionAmt', trade.get('size', 0))))
        leverage = trade.get('leverage', 1)
        side_emoji = "🟢" if side == 'LONG' else "🔴"
        text = (
            f"🔔 *Новая позиция открыта!*\n\n"
            f"{side_emoji} {symbol} — {side}\n"
            f"💵 Цена входа: ${entry:.4f}\n"
            f"📦 Размер: {size}\n"
            f"⚡️ Плечо: {leverage}x\n\n"
            f"*Напишите причину входа или нажмите «Пропустить»:*"
        )
        callback_id = trade.get('orderId') or trade.get('positionId') or 'no-id'
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Комментарий", callback_data=f"entry_reason_{callback_id}"),
             InlineKeyboardButton("⏭ Пропустить", callback_data="skip_entry_reason")]
        ])
        await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка уведомления об открытии: {e}")


async def _notify_closed_trade(bot, chat_id: str, trade: dict, pnl: float, trade_id: int = None):
    try:
        symbol = trade.get('symbol', '?')
        side = trade.get('side', '?')
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        exit_price = trade.get('exit_price', 0)
        text = (
            f"🔔 *Позиция закрыта!*\n\n"
            f"{pnl_emoji} {symbol} — {side}\n"
            f"💰 PNL: ${pnl:+.2f}\n"
            f"💵 Цена выхода: ${exit_price:.4f}\n\n"
            f"*Добавьте вывод или выберите сетап:*"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Добавить вывод", callback_data=f"exit_reason_{trade_id}"),
             InlineKeyboardButton("📊 Сетап", callback_data=f"setup_{trade_id}")],
            [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_comment")]
        ])

        for attempt in range(3):
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=keyboard)
                return
            except Exception as e:
                err_str = str(e).lower()
                if 'retry' in err_str or '429' in err_str or 'flood' in err_str:
                    if attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                raise
    except Exception as e:
        logger.error(f"Ошибка уведомления о закрытии: {e}")
