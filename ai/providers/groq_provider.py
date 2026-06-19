import logging
import os
import requests
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)

PROXY_URL = "socks5://127.0.0.1:1080"


class GroqProvider(BaseProvider):
    """Провайдер для Groq API (Llama 3.3 70B) через SOCKS5."""

    API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY не задан")
        self.model = "llama-3.3-70b-versatile"
        self.temperature = 0.7
        self.max_tokens = 1024

    def generate(self, prompt: str, context: dict = None) -> str:
        """Отправляет запрос к Groq через SOCKS5‑прокси."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        proxies = {"http": PROXY_URL, "https": PROXY_URL}

        try:
            response = requests.post(
                self.API_URL,
                headers=headers,
                json=payload,
                proxies=proxies,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"Groq API error: {response.status_code} {response.text}")
                return f"AI analysis unavailable: HTTP {response.status_code}"
        except Exception as e:
            logger.error(f"Groq API exception: {e}")
            return f"AI analysis unavailable: {str(e)}"