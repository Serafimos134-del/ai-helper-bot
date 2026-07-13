"""
services/exchange_api.py
Facade поверх Exchange Adapter Layer (services/exchanges/) — единственная
точка входа для аккаунтных данных биржи, которую использует весь
остальной код (AI Core, хендлеры, фоновые джобы). Ни один из них не
импортирует services/bingx_api.py напрямую для этих функций и не знает,
какая биржа реально используется у текущего пользователя.

Резолвинг — через тот же contextvars-паттерн, что уже применялся для
BingX-ключей (services/bingx_api.py, Этап 1 миграции): middleware
(core/user_context.py:resolve_user_context) резолвит адаптер и ключи
ОДИН раз на Telegram-апдейт, дальше весь код в рамках этого апдейта
просто вызывает свободные функции ниже. Явный параметр adapter=... в
каждой сигнатуре AI Core (ai/context_builder.py, ai/orchestrator.py,
ai/risk_profile.py, core/scheduler.py, handlers/*.py — 15+ мест)
потребовал бы менять сигнатуры повсеместно ради того, что и так уже
официально резолвится один раз за апдейт — тот же trade-off, что уже был
сделан для credentials, теперь просто расширен на "какой адаптер"
поверх "какие у него ключи".

ВАЖНО (см. clear_current_exchange): PTB по умолчанию обрабатывает
апдейты последовательно в одном таске — без явного clear() в начале
resolve_user_context() адаптер/ключи предыдущего пользователя остались
бы активны для следующего запроса без своих ключей.
"""

import contextvars

from services.exchanges.registry import get_adapter, DEFAULT_EXCHANGE

_adapter_var: contextvars.ContextVar = contextvars.ContextVar('current_exchange_adapter', default=None)


def set_current_exchange(exchange: str, api_key: str = None, secret_key: str = None) -> None:
    adapter = get_adapter(exchange)
    if api_key and secret_key:
        adapter.set_credentials(api_key, secret_key)
    else:
        adapter.clear_credentials()
    _adapter_var.set(adapter)


def clear_current_exchange() -> None:
    adapter = _adapter_var.get()
    if adapter is not None:
        adapter.clear_credentials()
    _adapter_var.set(None)


def set_owner_exchange() -> None:
    """Явно устанавливает глобальные .env-ключи владельца — единственное
    место, где это делается. Нужно ДВУМ путям:
    1. core/user_context.py:resolve_user_context — когда резолвится сам
       владелец (is_owner=True) и у него нет своих привязанных ключей
       (переходный период, чтобы не заблокировать себя самого).
    2. core/scheduler.py:auto_sync_job/update_pinned_status — owner-only
       фоновые джобы, которые НЕ проходят через resolve_user_context (это
       job_queue-колбэки, не апдейты Telegram) и без явного вызова
       остались бы вовсе без credentials, раз services/bingx_api.py:
       _get_credentials() больше не откатывается на .env неявно ни для
       кого (см. её докстринг — раньше это давало утечку чужого баланса
       любому пользователю без своих ключей)."""
    from services.bingx_api import BINGX_API_KEY, BINGX_SECRET_KEY
    set_current_exchange(DEFAULT_EXCHANGE, BINGX_API_KEY, BINGX_SECRET_KEY)


def _current():
    adapter = _adapter_var.get()
    if adapter is None:
        adapter = get_adapter(DEFAULT_EXCHANGE)
        _adapter_var.set(adapter)
    return adapter


async def validate_keys(exchange: str, api_key: str, secret_key: str) -> dict:
    return await get_adapter(exchange).validate_keys(api_key, secret_key)


async def get_balance() -> dict:
    return await _current().get_balance()


async def get_open_positions() -> dict:
    return await _current().get_open_positions()


async def get_closed_orders(symbol: str = '', limit: int = 20) -> dict:
    return await _current().get_closed_orders(symbol, limit)


async def get_recent_closed_positions(limit: int = 20) -> dict:
    return await _current().get_recent_closed_positions(limit)
