import asyncio
import json
import logging
import time
import hmac
import hashlib
from datetime import datetime, timezone
from services.database import Database
from services.ai_trading import AITradingAnalyzer
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

WS_URL = "wss://open-api-ws.bingx.com/ws"
PING_INTERVAL = 20  # секунд между пингами
RECONNECT_DELAY = 1  # начальная задержка переподключения
MAX_RECONNECT_DELAY = 60  # максимальная задержка

db = Database()
ai_analyzer = AITradingAnalyzer()


async def ws_sync_loop(bot, chat_id: str, api_key: str, secret_key: str):
    """WebSocket-синхронизация с BingX"""
    import websockets

    reconnect_delay = RECONNECT_DELAY

    while True:
        try:
            logger.info(f"WebSocket: подключение к BingX...")
            async with websockets.connect(
                WS_URL,
                ping_interval=None,  # мы сами управляем пингами
                close_timeout=5
            ) as ws:
                logger.info("WebSocket: соединение установлено")

                # Авторизация
                timestamp = str(int(time.time() * 1000))
                sign = hmac.new(
                    secret_key.encode(),
                    f"{timestamp}ACCOUNT_UPDATE".encode(),
                    hashlib.sha256
                ).hexdigest()

                auth_msg = {
                    "id": "auth",
                    "reqType": "sub",
                    "dataType": "ACCOUNT_UPDATE",
                    "apiKey": api_key,
                    "timestamp": timestamp,
                    "sign": sign
                }
                await ws.send(json.dumps(auth_msg))
                logger.info("WebSocket: запрос подписки отправлен")

                # Запускаем пингователь и слушатель параллельно
                async def pinger():
                    while True:
                        try:
                            await asyncio.sleep(PING_INTERVAL)
                            pong = await ws.ping()
                            await pong
                            logger.debug("WebSocket: ping/pong OK")
                        except Exception:
                            logger.warning("WebSocket: ping не удался, соединение разорвано")
                            break

                ping_task = asyncio.create_task(pinger())

                try:
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            data_type = data.get("dataType", "")

                            if data_type == "":
                                # Ответ на подписку
                                if data.get("code") == 0:
                                    logger.info("WebSocket: подписка на ACCOUNT_UPDATE подтверждена")
                                else:
                                    logger.error(f"WebSocket: ошибка подписки: {data}")
                                continue

                            if data_type == "ACCOUNT_UPDATE":
                                await handle_account_update(bot, chat_id, data)
                                continue

                            if data_type == "PING":
                                await ws.send(json.dumps({"dataType": "PONG"}))
                                continue

                            logger.debug(f"WebSocket: неизвестный тип данных: {data_type}")

                        except json.JSONDecodeError:
                            logger.warning(f"WebSocket: невалидный JSON: {message[:200]}")
                            continue

                finally:
                    ping_task.cancel()
                    try:
                        await ping_task
                    except asyncio.CancelledError:
                        pass

            # Успешное завершение — сбрасываем задержку
            reconnect_delay = RECONNECT_DELAY

        except asyncio.CancelledError:
            logger.info("WebSocket: остановлен")
            break
        except Exception as e:
            logger.error(f"WebSocket: ошибка соединения: {e}")
            logger.info(f"WebSocket: переподключение через {reconnect_delay} сек...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)


async def handle_account_update(bot, chat_id: str, data: dict):
    """Обработка обновления аккаунта"""
    try:
        positions = data.get("data", {}).get("positions", [])
        logger.debug(f"WebSocket: получено {len(positions)} позиций")

        if not positions:
            # Все позиции закрыты
            stored_open = db.get_open_trades()
            for stored in stored_open:
                await _close_position(bot, chat_id, stored)
            return

        stored_open = db.get_open_trades()
        stored_ids = {t.get('orderId') for t in stored_open if t.get('orderId')}

        for pos in positions:
            order_id = str(pos.get("orderId"))
            symbol = pos.get("symbol", "")
            side = "LONG" if pos.get("positionSide") == "LONG" else "SHORT"
            entry_price = float(pos.get("entryPrice", 0))
            quantity = abs(float(pos.get("positionAmt", 0)))
            unrealized_pnl = float(pos.get("unrealizedProfit", 0))
            leverage = float(pos.get("leverage", 1))
            stop_loss = pos.get("stopLoss")
            take_profit = pos.get("takeProfit")

            # Фильтр нулевых позиций
            if entry_price == 0 and quantity == 0:
                continue

            if order_id in stored_ids:
                # Обновляем существующую
                db.update_open_trade_by_order_id(
                    order_id,
                    unrealized_pnl=unrealized_pnl,
                    leverage=leverage,
                    quantity=quantity,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )
                stored_ids.discard(order_id)
            else:
                # Новая позиция
                if quantity > 0 and entry_price > 0:
                    db.add_open_trade({
                        'orderId': order_id,
                        'symbol': symbol,
                        'side': side,
                        'entry_price': entry_price,
                        'quantity': quantity,
                        'leverage': leverage,
                        'unrealized_pnl': unrealized_pnl,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'entry_comment': ''
                    })
                    logger.info(f"WebSocket: новая позиция {symbol} {side}")
                    await _notify_new_trade(bot, chat_id, pos)

        # Закрытые позиции
        for order_id in stored_ids:
            stored = next((t for t in stored_open if t.get('orderId') == order_id), None)
            if stored:
                await _close_position(bot, chat_id, stored)

    except Exception as e:
        logger.error(f"WebSocket: ошибка обработки обновления: {e}", exc_info=True)


async def _close_position(bot, chat_id: str, stored: dict):
    """Закрытие позиции: сохраняем в историю, уведомляем, запускаем AI-оценку"""
    try:
        symbol = stored.get('symbol', '?')
        side = stored.get('side', '?')

        closed_trade = {
            'symbol': symbol,
            'side': side,
            'entry_price': float(stored.get('entry_price', 0)),
            'exit_price': float(stored.get('entry_price', 0)),  # будет обновлено позже
            'quantity': float(stored.get('quantity', 0)),
            'realized_pnl': float(stored.get('unrealized_pnl', 0)),
            'comment': '',
            'leverage': float(stored.get('leverage', 1)),
            'stop_loss': stored.get('stop_loss'),
            'take_profit': stored.get('take_profit'),
            'risk_percent': 0,
            'risk_reward': None,
            'open_time': stored.get('created_at'),
            'close_time': datetime.now(timezone.utc).isoformat(),
            'entry_comment': stored.get('entry_comment', ''),
            'exit_comment': '',
            'ai_review': '',
            'holding_minutes': None,
            'btc_price': None,
            'eth_price': None,
            'market_trend': None,
            'setup_type': None,
            'mistakes': None,
            'ai_score': None
        }

        db.add_closed_trade(closed_trade)
        db.delete_open_trade_by_order_id(stored.get('orderId'))
        last_id = db.get_last_closed_id()

        logger.info(f"WebSocket: позиция закрыта {symbol} {side}")

        await _notify_closed_trade(bot, chat_id, stored, closed_trade['realized_pnl'], last_id)

        # Запускаем AI-оценку в фоне
        if last_id:
            asyncio.create_task(_auto_ai_review(last_id, closed_trade))

    except Exception as e:
        logger.error(f"WebSocket: ошибка закрытия позиции: {e}", exc_info=True)


async def _auto_ai_review(trade_id: int, closed_trade: dict):
    """Автоматическая AI-оценка сделки"""
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
        logger.info(f"WebSocket: AI-оценка для сделки #{trade_id} готова")
    except Exception as e:
        logger.error(f"WebSocket: ошибка AI-оценки для сделки {trade_id}: {e}")


async def _notify_new_trade(bot, chat_id: str, trade: dict):
    """Уведомление о новой позиции"""
    try:
        symbol = trade.get('symbol', '?')
        side = "LONG" if trade.get('positionSide') == 'LONG' else 'SHORT'
        entry = float(trade.get('entryPrice', 0))
        size = abs(float(trade.get('positionAmt', 0)))
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
            [
                InlineKeyboardButton("✏️ Комментарий", callback_data=f"entry_reason_{trade.get('orderId')}"),
                InlineKeyboardButton("⏭ Пропустить", callback_data="skip_entry_reason")
            ]
        ])

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        logger.info(f"WebSocket: уведомление о новой позиции отправлено")
    except Exception as e:
        logger.error(f"WebSocket: ошибка уведомления об открытии: {e}")


async def _notify_closed_trade(bot, chat_id: str, trade: dict, pnl: float, trade_id: int = None):
    """Уведомление о закрытии позиции"""
    try:
        symbol = trade.get('symbol', '?')
        side = trade.get('side', '?')
        pnl_emoji = "✅" if pnl >= 0 else "❌"

        pnl_pct_str = ""
        try:
            entry_price = float(trade.get('entry_price', 0))
            quantity = float(trade.get('quantity', 1))
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
            [
                InlineKeyboardButton("✏️ Добавить вывод", callback_data=f"exit_reason_{trade_id}"),
                InlineKeyboardButton("📊 Сетап", callback_data=f"setup_{trade_id}")
            ],
            [
                InlineKeyboardButton("⏭ Пропустить", callback_data="skip_comment")
            ]
        ])

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        logger.info(f"WebSocket: уведомление о закрытии #{trade_id} отправлено")
    except Exception as e:
        logger.error(f"WebSocket: ошибка уведомления о закрытии: {e}")