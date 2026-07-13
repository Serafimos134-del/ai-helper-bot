"""
core/ai_rate_limit.py
Per-user cooldown на AI-функции (задача от 13.07.2026, п.2 рекомендаций
аудита) — защита от "шумного соседа": без этого один подписчик, спамящий
Консилиум/AI-анализ/AI Coach в тесном цикле, мог бы в одиночку съесть общую
пропускную способность/дневную квоту Groq (единая на весь бот, см.
ai/providers/groq_provider.py) для всех остальных пользователей.

Общий per-user таймстамп на ВСЕ AI-функции разом (не отдельный счётчик на
каждую команду) — цель защитить общий бюджет запросов к Groq, а не конкретную
кнопку. In-memory, не персистентный — при рестарте бота счётчики обнуляются,
это приемлемо для UX-ограничения (не критичная для консистентности данных
величина), тот же trade-off, что уже принят для _sync_locks в
services/auto_sync.py.
"""

import os
import threading
import time

AI_COOLDOWN_SECONDS = float(os.getenv('AI_COOLDOWN_SECONDS', '10'))

_last_call: dict = {}
_lock = threading.Lock()


def check_ai_cooldown(user_id: str) -> float:
    """0.0, если AI-запрос можно выполнять прямо сейчас (и сразу резервирует
    слот на AI_COOLDOWN_SECONDS вперёд) — иначе сколько секунд ещё осталось
    ждать. Вызывающий код обязан проверить возврат ДО показа "⏳ Анализирую..."
    и любых сетевых вызовов."""
    now = time.monotonic()
    with _lock:
        last = _last_call.get(user_id)
        if last is not None:
            remaining = AI_COOLDOWN_SECONDS - (now - last)
            if remaining > 0:
                return round(remaining, 1)
        _last_call[user_id] = now
        return 0.0


def cooldown_message(wait_seconds: float) -> str:
    return (
        f"⏳ Подожди {wait_seconds:.0f}с — AI-функции ограничены по частоте, "
        f"чтобы общий лимит запросов к AI не расходовался одним пользователем."
    )
