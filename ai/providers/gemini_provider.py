import logging
import os
import requests
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)


class GeminiProvider(BaseProvider):
    """Провайдер для Google Gemini API (REST)."""

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY не задан")
        self.model = "gemini-2.0-flash"
        self.temperature = 0.7
        self.max_tokens = 1024

    def generate(self, prompt: str, context: dict = None) -> str:
        """Отправляет запрос к Gemini и возвращает текст ответа."""
        headers = {
            "Content-Type": "application/json",
            "X-goog-api-key": self.api_key,
        }
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            }
        }

        try:
            response = requests.post(
                self.API_URL,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                # Извлекаем текст из ответа Gemini
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
                return "AI analysis unavailable: no content in response"
            else:
                logger.error(f"Gemini API error: {response.status_code} {response.text}")
                return f"AI analysis unavailable: HTTP {response.status_code}"
        except Exception as e:
            logger.error(f"Gemini API exception: {e}")
            return f"AI analysis unavailable: {str(e)}"