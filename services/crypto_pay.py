"""
services/crypto_pay.py
Обёртка над Crypto Pay API (@CryptoBot) — приём оплаты подписки в USDT.

Токен берётся из переменной окружения CRYPTO_PAY_TOKEN (.env на сервере,
НЕ коммитить — см. .env.example). Статус инвойсов проверяется поллингом
(core/scheduler.py:crypto_pay_poll_job), а не вебхуком: бот работает через
long-polling (app.run_polling в bot.py), публичного HTTPS-эндпоинта для
пуш-уведомлений сейчас нет — заводить его только ради вебхука Crypto Pay
на этом этапе миграции избыточно.
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

CRYPTO_PAY_TOKEN = os.getenv('CRYPTO_PAY_TOKEN', '')
BASE_URL = 'https://pay.crypt.bot/api/'


async def _request(method: str, params: dict = None) -> dict:
    if not CRYPTO_PAY_TOKEN:
        logger.error("CRYPTO_PAY_TOKEN не задан — оплата недоступна")
        return {'ok': False, 'error': {'name': 'NO_TOKEN'}}
    headers = {'Crypto-Pay-API-Token': CRYPTO_PAY_TOKEN}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(BASE_URL + method, json=params or {}, headers=headers)
            return resp.json()
    except Exception as e:
        logger.error(f"Crypto Pay API ошибка ({method}): {e}")
        return {'ok': False, 'error': {'name': 'REQUEST_FAILED', 'message': str(e)}}


async def create_invoice(amount: float, description: str, payload: str, asset: str = 'USDT') -> dict:
    """Создаёт счёт на оплату. payload — user_id получателя (для сверки
    при начислении, хотя основная сверка идёт по invoice_id в БД)."""
    result = await _request('createInvoice', {
        'asset': asset,
        'amount': amount,
        'description': description,
        'payload': payload,
        'expires_in': 3600,
    })
    if not result.get('ok'):
        return {'success': False, 'error': result.get('error', {}).get('name', 'unknown')}
    inv = result['result']
    return {
        'success': True,
        'invoice_id': inv['invoice_id'],
        'pay_url': inv.get('bot_invoice_url') or inv.get('pay_url'),
    }


async def get_invoice_statuses(invoice_ids: list) -> dict:
    """Возвращает {invoice_id (str): status} для переданных ID
    ('active'/'paid'/'expired'). Пустой словарь при ошибке запроса —
    вызывающий код (poll job) просто попробует снова на следующем тике."""
    if not invoice_ids:
        return {}
    result = await _request('getInvoices', {
        'invoice_ids': ','.join(str(i) for i in invoice_ids)
    })
    if not result.get('ok'):
        logger.warning(f"Crypto Pay getInvoices ошибка: {result.get('error')}")
        return {}
    items = result.get('result', {}).get('items', [])
    return {str(item['invoice_id']): item.get('status') for item in items}
