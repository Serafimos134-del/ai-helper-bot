from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """Абстрактный базовый класс для AI-провайдеров."""

    @abstractmethod
    def generate(self, prompt: str, context: dict = None) -> str:
        """
        Отправляет запрос к AI-модели и возвращает текстовый ответ.

        Args:
            prompt: Текст запроса (может включать инструкции и данные)
            context: Опциональный словарь с дополнительным контекстом

        Returns:
            Строка с ответом модели
        """
        pass