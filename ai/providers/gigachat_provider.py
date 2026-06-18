import logging
import os
import requests
import base64
import time
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)


class GigaChatProvider(BaseProvider):
    """Провайдер для GigaChat API (Сбер)."""

    AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GIGACHAT_API_KEY")
        if not self.api_key:
            raise ValueError("GIGACHAT_API_KEY не задан")
        self.access_token = None
        self.token_expiry = 0
        self.model = "GigaChat"
        self.temperature = 0.7
        self.max_tokens = 1024

    def _get_token(self) -> str:
        """Получает или обновляет access token."""
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token

        try:
            # Ключ в формате base64(client_id:client_secret)
            auth_header = f"Bearer {self.api_key}"
            headers = {
                "Authorization": auth_header,
                "Content-Type": "application/x-www-form-urlencoded"
            }
            data = {"scope": "GIGACHAT_API_PERS"}

            response = requests.post(
                self.AUTH_URL,
                headers=headers,
                data=data,
                timeout=10
            )

            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data["access_token"]
                # Токен живёт 30 минут, обновим за 5 минут до истечения
                self.token_expiry = time.time() + token_data.get("expires_in", 1800) - 300
                logger.info("GigaChat access token получен")
                return self.access_token
            else:
                logger.error(f"GigaChat auth error: {response.status_code} {response.text}")
                raise Exception(f"Auth failed: {response.status_code}")
        except Exception as e:
            logger.error(f"GigaChat auth exception: {e}")
            raise

    def generate(self, prompt: str, context: dict = None) -> str:
        """Отправляет запрос к GigaChat и возвращает ответ."""
        try:
            token = self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens
            }

            response = requests.post(
                self.API_URL,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"GigaChat API error: {response.status_code} {response.text}")
                return f"AI analysis unavailable: HTTP {response.status_code}"
        except Exception as e:
            logger.error(f"GigaChat API exception: {e}")
            return f"AI analysis unavailable: {str(e)}"