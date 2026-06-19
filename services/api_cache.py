import time
import asyncio
import logging
from functools import partial

logger = logging.getLogger(__name__)


class APICache:
    """
    Простой in-memory кэш для API-запросов с TTL.
    Не требует внешних зависимостей.
    """

    # TTL по умолчанию для разных типов данных (секунды)
    DEFAULT_TTL = {
        'ticker': 10,
        'balance': 5,
        'positions': 5,
        'top_tickers': 15,
        'kline': 30,
    }

    def __init__(self):
        self._cache = {}          # ключ → (timestamp, value)
        self._lock = asyncio.Lock()

    def _make_key(self, func_name: str, args: tuple) -> str:
        """Создаёт строковый ключ для кэша."""
        return f"{func_name}:{str(args)}"

    def _get_ttl(self, func_name: str) -> int:
        """Возвращает TTL для типа запроса."""
        for prefix, ttl in self.DEFAULT_TTL.items():
            if func_name.startswith(prefix) or prefix in func_name:
                return ttl
        return 15  # default

    async def get(self, func_name: str, args: tuple = ()) -> any:
        """
        Возвращает значение из кэша, если оно есть и не истекло.
        Иначе возвращает None.
        """
        async with self._lock:
            key = self._make_key(func_name, args)
            entry = self._cache.get(key)
            if entry is None:
                return None
            timestamp, value = entry
            ttl = self._get_ttl(func_name)
            if time.time() - timestamp > ttl:
                del self._cache[key]
                return None
            return value

    async def set(self, func_name: str, args: tuple, value: any):
        """Сохраняет значение в кэш."""
        async with self._lock:
            key = self._make_key(func_name, args)
            self._cache[key] = (time.time(), value)

    async def get_or_fetch(self, func, *args, force_refresh=False):
        """
        Возвращает значение из кэша или вызывает функцию, сохраняет и возвращает.
        func — синхронная функция.
        """
        func_name = func.__name__
        if not force_refresh:
            cached = await self.get(func_name, args)
            if cached is not None:
                logger.debug(f"Cache HIT: {func_name}{args}")
                return cached

        logger.debug(f"Cache MISS: {func_name}{args}")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, partial(func, *args))
        await self.set(func_name, args, result)
        return result

    async def invalidate(self, func_name: str = None):
        """Сбрасывает кэш для указанной функции или весь кэш."""
        async with self._lock:
            if func_name is None:
                self._cache.clear()
            else:
                keys_to_delete = [k for k in self._cache if k.startswith(func_name)]
                for k in keys_to_delete:
                    del self._cache[k]

    def size(self) -> int:
        """Возвращает количество записей в кэше."""
        return len(self._cache)


# Глобальный экземпляр кэша
api_cache = APICache()