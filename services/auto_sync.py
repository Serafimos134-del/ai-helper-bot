import asyncio
import logging
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

async def sync_trades(bot, chat_id: str) -> dict:
    results = {'new_open': [], 'new_closed': []}

    db.cleanup_orphan_open_trades()

    open_result = get_open_positions()
    if not open_result.get('success'):
        logger.warning(f"Ошибка получения открытых позиций: {open_result.get('error')}")
        return results

    api_trades = open_result.get('trades', [])
    stored_open = db.get_open_trades()
    stored_by_id = {}
    for t in stored_open:
        oid = str(t.get('orderId')) if t.get('orderId') else None
        if oid:
            stored_by_id[oid] = t

    for trade in api_trades:
        oid = str(trade.get('orderId'))
        raw_side = trade.get('side', '')
        side = 'LONG' if raw_side in ('BUY', 'LONG') else 'SHORT'

        if oid in stored_by_id:
            db.update_open_trade_by_order_id(
                oid,
                unrealized_pnl=float(trade.get('unrealizedPnl', 0)),
                leverage=float(trade.get('leverage', 1)),
                quantity=abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                entry_price=float(trade.get('entryPrice', 0)),
                stop_loss=trade.get('stopLoss'),
                take_profit=trade.get('takeProfit')
            )
            stored_by_id.pop(oid)
        else:
            db.add_open_trade({
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

    # Получаем провайдер AI один раз
    ai_provider = AITradingAnalyzer().provider
    # Создаём ConsensusEngine один раз для всех закрытых сделок
    engine = ConsensusEngine(ai_provider)

    for oid, stored in stored_by_id.items():
        closed_trade = _build_closed_trade(stored)
        db.add_closed_trade(closed_trade)
        db.delete_open_trade_by_order_id(oid)
        last_id = db.get_last_closed_id()

        # --- Анализ через Consensus Engine ---
        try:
            analysis = await engine.analyze_closed_trade(closed_trade)
            # trade_score берём отдельно из TradeScorer
            score = trade_scorer.score(closed_trade)
            db.update_trade_metrics(last_id,
                                    ai_score=score['total_score'],
                                    market_review=analysis['market_review'],
                                    risk_review=analysis['risk_review'],
                                    psychology_review=analysis['psychology_review'],
                                    judge_verdict=analysis['judge_verdict'])
            logger.info(f"Сделка #{last_id} проанализирована консилиумом: {analysis['judge_verdict']}")
        except Exception as e:
            logger.error(f"Ошибка консилиума для сделки #{last_id}: {e}")
            # fallback — старый скоринг
            try:
                score = trade_scorer.score(closed_trade)
                db.update_trade_metrics(last_id, ai_score=score['total_score'])
                logger.info(f"Сделка #{last_id} оценена (fallback): {score['total_score']}/10")
            except Exception as fallback_e:
                logger.error(f"Ошибка даже fallback-оценки для сделки #{last_id}: {fallback_e}")

        results['new_closed'].append(stored)
        await _notify_closed_trade(bot, chat_id, stored, closed_trade['realized_pnl'], last_id)

    return results


def _build_closed_trade(stored_open: dict) -> dict:
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

    # Вычисляем реальную цену выхода через PnL
    exit_price = _calculate_exit_price(stored_open)

    return {
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
        text = (
            f"🔔 *Позиция закрыта!*\n\n"
            f"{pnl_emoji} {symbol} — {side}\n"
            f"💰 PNL: ${pnl:+.2f}\n\n"
            f"*Добавьте вывод или выберите сетап:*"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Добавить вывод", callback_data=f"exit_reason_{trade_id}"),
             InlineKeyboardButton("📊 Сетап", callback_data=f"setup_{trade_id}")],
            [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_comment")]
        ])
        await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка уведомления о закрытии: {e}")