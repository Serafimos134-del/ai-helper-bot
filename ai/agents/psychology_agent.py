import asyncio
import logging
import json
from ai.context_builder import ContextBuilder
from ai.psychology_engine import PsychologyEngine

logger = logging.getLogger(__name__)


class PsychologyAgent:
    """Агент, анализирующий психологические паттерны трейдера (rule‑based, без LLM)."""

    def __init__(self, provider=None):
        self.provider = None  # больше не используется
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        """Возвращает JSON с психологическим профилем."""
        if context is None:
            ctx = await self.context_builder.build_full_context()
        else:
            ctx = context
        history = ctx.get("history", {})

        # 1. Получаем сигналы от PsychologyEngine
        signals = PsychologyEngine.assess(history)

        # 2. Генерируем summary из шаблона (без LLM)
        signals["summary"] = self._build_template_summary(signals)

        return json.dumps(signals, ensure_ascii=False, indent=2)

    @staticmethod
    def _build_template_summary(signals: dict) -> str:
        """Генерирует summary на основе флагов и метрик без LLM."""
        flags = signals.get("flags", [])
        score = signals.get("psychology_score", 50)

        if not flags:
            return "Психологическое состояние стабильное. Отклонений не обнаружено."

        messages = []
        flag_map = {
            "overtrading": "Обнаружен риск овертрейдинга. Снизить частоту входов и увеличить время между сделками.",
            "revenge_trading": "Признаки revenge trading. Рекомендуется пауза минимум 24 часа.",
            "tilt": "Высокая вероятность тильта. Эмоциональные решения преобладают. Сделать перерыв.",
            "fomo": "Замечен FOMO-паттерн. Входы без подтверждения сигналов. Пересмотреть критерии входа.",
            "high_stress": "Повышенный уровень стресса. Новые сделки не рекомендуются до стабилизации.",
        }

        for flag in flags:
            if flag in flag_map:
                messages.append(flag_map[flag])
            else:
                messages.append(f"Обнаружен флаг: {flag}.")

        if score < 40:
            messages.append("Психологический счёт критически низкий. Настоятельно рекомендуется пауза в торговле.")
        elif score < 60:
            messages.append("Психологический счёт ниже нормы. Требуется осознанный контроль эмоций.")

        return " | ".join(messages)