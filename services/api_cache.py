import time
import asyncio
import logging
from functools import partial

logger = logging.getLogger(__name__)


class APICache:
    """
    Простой in-memory кэш для API-запросов с TTL.
    """

    DEFAULT_TTL = {
        'ticker': 10,
        'funding': 10,
        'oi': 10,
        'balance': 5,
        'positions': 5,
        'top_tickers': 15,
        'kline': 30,
    }

    def __init__(self):
        self._cache = {}
        self._lock = asyncio.Lock()

    def _get_ttl(self, key: str) -> int:
        for prefix, ttl in self.DEFAULT_TTL.items():
            if key.startswith(prefix):
                return ttl
        return 15

    async def get(self, key: str) -> any:
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            timestamp, value, ttl_override = entry
            ttl = ttl_override if ttl_override is not None else self._get_ttl(key)
            if time.time() - timestamp > ttl:
                del self._cache[key]
                return None
            return value

    async def set(self, key: str, value: any, ttl: int = None):
        """ttl (в секундах) — необязательное явное переопределение времени жизни
        записи; если не задано, используется DEFAULT_TTL по префиксу ключа."""
        async with self._lock:
            self._cache[key] = (time.time(), value, ttl)

    async def invalidate(self, prefix: str = None):
        async with self._lock:
            if prefix is None:
                self._cache.clear()
            else:
                keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
                for k in keys_to_delete:
                    del self._cache[k]

    def size(self) -> int:
        return len(self._cache)


api_cache = APICache()