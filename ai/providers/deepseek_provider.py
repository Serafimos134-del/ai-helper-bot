import logging
import os
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


class DeepSeekProvider(BaseProvider):
    """Провайдер для DeepSeek API (совместим с OpenAI SDK)."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY не задан")
        if OpenAI is None:
            raise ImportError("Пакет openai не установлен. Выполните: pip install openai")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com"
        )
        self.model = "deepseek-chat"
        self.temperature = 0.7
        self.max_tokens = 1024

    def generate(self, prompt: str, context: dict = None) -> str:
        """Отправляет запрос к DeepSeek и возвращает ответ."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"DeepSeek API error: {e}")
            return f"AI analysis unavailable: {str(e)}"