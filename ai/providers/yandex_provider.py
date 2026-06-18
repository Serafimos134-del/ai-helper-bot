import logging
import os
import requests
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)


class YandexProvider(BaseProvider):
    """Провайдер для YandexGPT API (Yandex AI Studio)."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("YANDEX_API_KEY")
        if not self.api_key:
            raise ValueError("YANDEX_API_KEY не задан")
        self.model = "yandexgpt-lite"
        self.temperature = 0.7
        self.max_tokens = 1000
        self.url = "https://api.ai.studio.yandex.net/v1/chat/completions"

    def generate(self, prompt: str, context: dict = None) -> str:
        """Отправляет запрос к YandexGPT и возвращает ответ."""
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

        try:
            response = requests.post(self.url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"YandexGPT API error: {response.status_code} {response.text}")
                return f"AI analysis unavailable: HTTP {response.status_code}"
        except Exception as e:
            logger.error(f"YandexGPT API exception: {e}")
            return f"AI analysis unavailable: {str(e)}"