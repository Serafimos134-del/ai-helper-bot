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

# Default rate limit: minimum interval between requests (seconds)
_RATE_LIMIT_DELAY = 2.0
_last_request_time = 0.0
_rate_lock = threading.Lock()

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0  # initial backoff in seconds (will be multiplied by 2 each retry)
RETRYABLE_STATUSES = {429, 502, 503, 504}


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
        global _last_request_time

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
            # Apply rate limiting (thread-safe)
            with _rate_lock:
                now = time.time()
                elapsed = now - _last_request_time
                if elapsed < _RATE_LIMIT_DELAY:
                    sleep_time = _RATE_LIMIT_DELAY - elapsed
                    time.sleep(sleep_time)
                _last_request_time = time.time()

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