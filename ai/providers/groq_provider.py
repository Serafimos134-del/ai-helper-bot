import logging
from groq import Groq
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)


class GroqProvider(BaseProvider):
    """Провайдер для Groq API (Llama 3.3 70B)."""

    def __init__(self, api_key: str):
        self.client = Groq(api_key=api_key)
        self.model = "llama-3.3-70b-versatile"
        self.temperature = 0.7
        self.max_tokens = 1024

    def generate(self, prompt: str, context: dict = None) -> str:
        """
        Отправляет запрос к Groq и возвращает ответ.

        Args:
            prompt: Текст запроса
            context: Дополнительный контекст (пока не используется, но будет
                     задействован в будущем Context Builder)

        Returns:
            Ответ модели или сообщение об ошибке
        """
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