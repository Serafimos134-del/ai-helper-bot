"""
diag_bybit.py — диагностика ключей Bybit напрямую с сервера, в обход бота
и Telegram (задача от 13.07.2026 — "давай с сервера запросим"). Показывает
СЫРОЙ ответ Bybit (HTTP-статус + полное тело), а не то, что успевает
показать бот после форматирования под Telegram-сообщение.

Запуск на сервере (там, где стоит сам бот, с уже установленными
зависимостями):
    cd /opt/ai-helper-bot  # или актуальный путь деплоя
    python3 diag_bybit.py <API_KEY> <SECRET_KEY>

Ключи НЕ сохраняются никуда и никуда не логируются, кроме терминала —
только для однократной ручной диагностики.
"""
import asyncio
import sys

sys.path.insert(0, '.')


async def main():
    if len(sys.argv) != 3:
        print("Использование: python3 diag_bybit.py <API_KEY> <SECRET_KEY>")
        sys.exit(1)

    api_key, secret_key = sys.argv[1], sys.argv[2]

    import services.bybit_api as bybit_api
    bybit_api.set_bybit_credentials(api_key, secret_key)

    print(f"API Key: {api_key}")
    print(f"Base URL: {bybit_api.BASE_URL}")
    print()

    print("=== RAW /v5/account/wallet-balance (accountType=UNIFIED) ===")
    raw = await bybit_api._request('/v5/account/wallet-balance', {'accountType': 'UNIFIED'})
    print(raw)
    print()

    print("=== get_balance() (через обёртку адаптера) ===")
    balance = await bybit_api.get_balance()
    print(balance)
    print()

    if balance.get('success'):
        print("=== get_open_positions() ===")
        positions = await bybit_api.get_open_positions()
        print(positions)


asyncio.run(main())
