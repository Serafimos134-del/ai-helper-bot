"""
ai/providers/groq_provider.py
Refactored Groq provider with thread-safe rate limiting, retry logic,
configurable parameters, and structured logging.
"""

import logging
import os
import time
import threading
import requests
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)

PROXY_URL = "socks5://127.0.0.1:1080"

# Раньше здесь был фиксированный интервал "1 запрос / 2с на весь процесс"
# (threading.Lock + time.sleep(2) внутри него) — независимо от того, сколько
# реально разрешает аккаунт Groq. Эмпирически (нагрузочный тест на реальном
# коде rate-limiter'а, без обращения к настоящему API) это давало задержку
# ~2с на каждого ДОПОЛНИТЕЛЬНОГО одновременного пользователя — 40
# параллельных AI-запросов означали ~80с ожидания для последнего в очереди,
# при том что сам Groq не был узким местом (см. отчёт аудита от 13.07.2026).
#
# Token bucket, откалиброванный под реальную квоту аккаунта — можно
# добираться до фактического лимита короткими всплесками, а не только по
# одному запросу раз в 2с. GROQ_RPM по умолчанию — консервативное значение
# под Free tier Groq (30 RPM у llama-3.3-70b-versatile на середину 2026,
# см. console.groq.com/docs/rate-limits) с небольшим запасом; на платном
# тарифе лимит выше — поднять через переменную окружения GROQ_RPM.
GROQ_RPM = int(os.getenv('GROQ_RPM', '28'))
GROQ_BURST = int(os.getenv('GROQ_BURST', str(min(GROQ_RPM, 10))))

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0  # initial backoff in seconds (will be multiplied by 2 each retry)
RETRYABLE_STATUSES = {429, 502, 503, 504}


class _TokenBucket:
    """Потокобезопасный token bucket — вызывается из воркер-тредов
    ThreadPoolExecutor (run_in_executor), поэтому обычные threading-примитивы,
    не asyncio. capacity — сколько запросов можно сделать одним всплеском
    без ожидания; rate_per_sec — скорость восполнения после этого."""

    def __init__(self, rate_per_sec: float, capacity: float):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = capacity
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


_bucket = _TokenBucket(rate_per_sec=GROQ_RPM / 60.0, capacity=GROQ_BURST)


class GroqProviderError(Exception):
    """Custom exception for Groq provider failures."""
    pass


class GroqProvider(BaseProvider):
    """Provider for Groq API (Llama 3.3 70B) via SOCKS5 proxy."""

    API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY не задан")
        self.model = "llama-3.3-70b-versatile"
        self.default_temperature = 0.7
        self.default_max_tokens = 512

    def generate(self, prompt: str, context: dict = None,
                 temperature: float = None, max_tokens: int = None) -> str:
        """
        Send a request to Groq with rate limiting and retry logic.
        Returns the model's response text.
        """
        temp = temperature if temperature is not None else self.default_temperature
        max_tok = max_tokens if max_tokens is not None else self.default_max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temp,
            "max_tokens": max_tok
        }
        proxies = {"http": PROXY_URL, "https": PROXY_URL}

        last_exception = None
        for attempt in range(MAX_RETRIES + 1):
            # Token bucket вместо фиксированной паузы — см. комментарий у
            # GROQ_RPM выше. Ретраи одного и того же запроса тоже проходят
            # через бакет, а не идут в обход него.
            _bucket.acquire()

            start_time = time.time()
            try:
                logger.debug(f"Groq request attempt {attempt+1}/{MAX_RETRIES+1}, prompt_len={len(prompt)}")
                response = requests.post(
                    self.API_URL,
                    headers=headers,
                    json=payload,
                    proxies=proxies,
                    timeout=30
                )
                duration = time.time() - start_time
                logger.info(f"Groq response: {response.status_code}, duration={duration:.2f}s")

                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    return content
                elif response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                    backoff = RETRY_BACKOFF * (2 ** attempt)
                    logger.warning(
                        f"Groq retryable error {response.status_code}, "
                        f"retrying in {backoff:.1f}s (attempt {attempt+1})"
                    )
                    time.sleep(backoff)
                    last_exception = GroqProviderError(
                        f"Groq API error: {response.status_code} {response.text[:200]}"
                    )
                    continue
                else:
                    # Non-retryable or retries exhausted
                    logger.error(f"Groq API fatal error: {response.status_code} {response.text[:500]}")
                    raise GroqProviderError(
                        f"Groq API error: {response.status_code} {response.text[:200]}"
                    )
            except requests.exceptions.Timeout as e:
                logger.warning(f"Groq timeout (attempt {attempt+1}): {e}")
                if attempt < MAX_RETRIES:
                    backoff = RETRY_BACKOFF * (2 ** attempt)
                    time.sleep(backoff)
                    last_exception = e
                    continue
                last_exception = e
                break
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Groq connection error (attempt {attempt+1}): {e}")
                if attempt < MAX_RETRIES:
                    backoff = RETRY_BACKOFF * (2 ** attempt)
                    time.sleep(backoff)
                    last_exception = e
                    continue
                last_exception = e
                break
            except Exception as e:
                logger.error(f"Unexpected error during Groq request: {e}")
                if attempt < MAX_RETRIES:
                    backoff = RETRY_BACKOFF * (2 ** attempt)
                    time.sleep(backoff)
                    last_exception = e
                    continue
                last_exception = e
                break

        # Все ретраи исчерпаны или ошибка не подлежит повтору.
        # Раньше здесь возвращалась строка "AI analysis unavailable: ...", которую
        # часть вызывающего кода не проверяла — неудачный запрос выглядел как
        # валидный ответ модели. Все текущие вызовы .generate() уже оборачивают
        # его в try/except, так что кидать исключение безопасно и корректно.
        error_msg = f"AI analysis unavailable: {last_exception}"
        logger.error(error_msg)
        if isinstance(last_exception, GroqProviderError):
            raise last_exception
        raise GroqProviderError(error_msg) from last_exception