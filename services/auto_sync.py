import asyncio
import logging
from datetime import datetime, timezone
from services.bingx_api import get_open_positions, get_closed_orders, get_ticker
from services.database import Database
from services.ai_trading import AITradingAnalyzer
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

db = Database()
ai_analyzer = AITradingAnalyzer()

async def sync_trades(bot, chat_id: str) -> dict:
    results = {'new_open': [], 'new_closed': []}

    # Очистка устаревших записей без orderId (страховка от старых дубликатов)
    db.cleanup_orphan_open_trades()

    # --- Открытые позиции ---
    open_result = get_open_positions()
    if not open_result.get('success'):
        logger.warning(f"Ошибка получения открытых позиций: {open_result.get('error')}")
    else:
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
                # Обновляем существующую позицию
                db.update_open_trade_by_order_id(
                    oid,
                    unrealized_pnl=float(trade.get('unrealizedPnl', 0)),
                    leverage=float(trade.get('leverage', 1)),
                    quantity=abs(float(trade.get('positionAmt', trade.get('size', 0)))),
                    entry_price=float(trade.get('entryPrice', 0)),
                    stop_loss=trade.get('stopLoss'),
                    take_profit=trade.get('takeProfit')
                )
                stored_by_id.pop(oid)  # чтобы не удалить как закрытую
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

        # Закрытые позиции – те, что остались в stored_by_id
        for oid, stored in stored_by_id.items():
            closed_trade = _build_closed_trade(stored)
            db.add_closed_trade(closed_trade)
            db.delete_open_trade_by_order_id(oid)
            last_id = db.get_last_closed_id()
            results['new_closed'].append(stored)
            await _notify_closed_trade(bot, chat_id, stored, closed_trade['realized_pnl'], last_id)
            asyncio.ensure_future(_auto_ai_review(last_id, closed_trade))

    # --- История закрытых ордеров (теперь сохраняются все, включая нулевые) ---
    closed_result = get_closed_orders(limit=50)
    if closed_result.get('success'):
        stored_closed = db.get_closed_trades(limit=1000)
        stored_closed_ids = {str(t.get('orderId', t.get('id'))) for t in stored_closed}
        for order in closed_result.get('trades', []):
            oid = str(order.get('orderId'))
            if oid not in stored_closed_ids:               # <-- убран фильтр profit != 0
                raw_side = order.get('side', 'BUY')
                side = 'LONG' if raw_side in ('BUY', 'LONG') else 'SHORT'
                open_time = order.get('time')
                close_time = order.get('updateTime')
                holding_minutes = None
                if open_time and close_time:
                    try:
                        diff = (close_time - open_time) / 1000 / 60
                        holding_minutes = int(diff)
                    except Exception:
                        pass
                btc_price, eth_price, market_trend = _get_market_data()
                db.add_closed_trade({
                    'symbol': order.get('symbol'),
                    'side': side,
                    'entry_price': float(order.get('avgPrice', 0)),
                    'exit_price': float(order.get('avgPrice', 0)),
                    'quantity': float(order.get('executedQty', 0)),
                    'realized_pnl': float(order.get('profit', 0)),
                    'comment': '',
                    'leverage': float(order.get('leverage', 1)),
                    'stop_loss': None,
                    'take_profit': None,
                    'risk_percent': 0,
                    'risk_reward': None,
                    'open_time': open_time,
                    'close_time': close_time,
                    'entry_comment': '',
                    'exit_comment': '',
                    'ai_review': '',
                    'holding_minutes': holding_minutes,
                    'btc_price': btc_price,
                    'eth_price': eth_price,
                    'market_trend': market_trend,
                    'setup_type': None,
                    'mistakes': None,
                    'ai_score': None
                })
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
    btc_price, eth_price, market_trend = _get_market_data()
    return {
        'symbol': stored_open['symbol'],
        'side': stored_open['side'],
        'entry_price': float(stored_open.get('entry_price', 0)),
        'exit_price': float(stored_open.get('entry_price', 0)),
        'quantity': float(stored_open.get('quantity', 0)),
        'realized_pnl': float(stored_open.get('unrealized_pnl', 0)),
        'leverage': float(stored_open.get('leverage', 1)),
        'stop_loss': stored_open.get('stop_loss'),
        'take_profit': stored_open.get('take_profit'),
        'open_time': open_time,
        'close_time': close_time,
        'entry_comment': stored_open.get('entry_comment', ''),
        'exit_comment': '',
        'ai_review': '',
        'holding_minutes': holding_minutes,
        'btc_price': btc_price,
        'eth_price': eth_price,
        'market_trend': market_trend,
        'setup_type': None,
        'mistakes': None,
        'ai_score': None
    }


def _get_market_data() -> tuple:
    btc_price = None
    eth_price = None
    market_trend = None
    try:
        btc_ticker = get_ticker("BTC-USDT")
        eth_ticker = get_ticker("ETH-USDT")
        if btc_ticker.get('success'):
            btc_price = float(btc_ticker['ticker'].get('lastPrice', 0))
            change = float(btc_ticker['ticker'].get('priceChangePercent', 0))
            if change > 1:
                market_trend = "BULLISH"
            elif change < -1:
                market_trend = "BEARISH"
            else:
                market_trend = "SIDEWAYS"
        if eth_ticker.get('success'):
            eth_price = float(eth_ticker['ticker'].get('lastPrice', 0))
    except Exception as e:
        logger.error(f"Ошибка получения рыночных данных: {e}")
    return btc_price, eth_price, market_trend


async def _auto_ai_review(trade_id: int, closed_trade: dict):
    try:
        prompt = (
            f"Дай краткую оценку сделке (2-3 предложения): что хорошо, что плохо, оценка от 1 до 10.\n"
            f"Символ: {closed_trade['symbol']}, сторона: {closed_trade['side']}, "
            f"вход: {closed_trade['entry_price']}, выход: {closed_trade['exit_price']}, "
            f"плечо: {closed_trade.get('leverage', 1)}, PNL: {closed_trade['realized_pnl']:.2f}.\n"
            f"Причина входа: {closed_trade.get('entry_comment', 'не указана')}."
        )
        review = ai_analyzer.analyze_raw(prompt)
        import re
        match = re.search(r'(\d+)\s*/\s*10', review)
        ai_score = int(match.group(1)) if match else None
        db.update_trade_metrics(trade_id, ai_review=review, ai_score=ai_score)
    except Exception as e:
        logger.error(f"Ошибка автоматической AI-оценки для сделки {trade_id}: {e}")


async def _notify_new_trade(bot, chat_id: str, trade: dict):
    try:
        symbol = trade.get('symbol', '?')
        side = trade.get('side', '?')
        entry = float(trade.get('entryPrice', 0))
        size = trade.get('size', trade.get('positionAmt', '?'))
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
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Комментарий", callback_data=f"entry_reason_{trade.get('orderId')}"),
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
        pnl_pct_str = ""
        try:
            entry_price = float(trade.get('entryPrice', 0))
            quantity = float(trade.get('size', trade.get('quantity', 1)))
            leverage = float(trade.get('leverage', 1))
            if entry_price > 0 and quantity > 0:
                margin = (entry_price * quantity) / leverage
                if margin != 0:
                    pnl_pct = (pnl / margin) * 100
                    pnl_pct_str = f"\n📈 PNL: {pnl_pct:+.1f}%"
        except Exception:
            pass
        text = (
            f"🔔 *Позиция закрыта!*\n\n"
            f"{pnl_emoji} {symbol} — {side}\n"
            f"💰 PNL: ${pnl:+.2f}{pnl_pct_str}\n\n"
            f"*AI-оценка будет готова через несколько секунд.*\n"
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


def _get_exit_price_for(order_id: str, stored_open: dict) -> float:
    return float(stored_open.get('entry_price', 0))