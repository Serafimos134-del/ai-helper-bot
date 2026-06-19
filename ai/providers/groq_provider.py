import logging
import os
from groq import Groq
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)

# SOCKS5‑прокси для обхода блокировок
PROXY_URL = "socks5://127.0.0.1:1080"


class GroqProvider(BaseProvider):
    """Провайдер для Groq API (Llama 3.3 70B) с поддержкой SOCKS5."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY не задан")
        # Создаём HTTP-клиент с прокси
        import httpx
        transport = httpx.HTTPTransport(proxy=PROXY_URL)
        http_client = httpx.Client(transport=transport)
        self.client = Groq(api_key=self.api_key, http_client=http_client)
        self.model = "llama-3.3-70b-versatile"
        self.temperature = 0.7
        self.max_tokens = 1024

    def generate(self, prompt: str, context: dict = None) -> str:
        """Отправляет запрос к Groq и возвращает ответ."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq API error: {e}")
            return f"AI analysis unavailable: {str(e)}"