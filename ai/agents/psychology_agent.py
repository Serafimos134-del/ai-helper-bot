import asyncio
import logging
import json
from ai.providers.base_provider import BaseProvider
from ai.context_builder import ContextBuilder
from ai.psychology_engine import PsychologyEngine

logger = logging.getLogger(__name__)


class PsychologyAgent:
    """Агент, анализирующий психологические паттерны трейдера (rule‑based)."""

    def __init__(self, provider: BaseProvider = None):
        self.provider = provider  # опционален, для LLM‑summary
        self.context_builder = ContextBuilder()

    async def analyze(self, context: dict = None) -> str:
        """Возвращает JSON с психологическим профилем."""
        if context is None:
            ctx = self.context_builder.build_full_context()
        else:
            ctx = context
        history = ctx.get("history", {})

        # 1. Получаем сигналы от PsychologyEngine
        signals = PsychologyEngine.assess(history)

        # 2. Если есть провайдер, можем улучшить summary через LLM (опционально)
        if self.provider and signals.get("flags"):
            try:
                enhanced = await self._enhance_summary(signals)
                if enhanced:
                    signals["summary"] = enhanced
            except Exception as e:
                logger.error(f"Ошибка LLM‑summary: {e}")

        return json.dumps(signals, ensure_ascii=False, indent=2)

    async def _enhance_summary(self, signals: dict) -> str:
        """Опциональное улучшение summary через LLM (асинхронно)."""
        if not self.provider:
            return ""
        prompt = (
            f"Ты — спортивный психолог. На основе флагов и метрик дай краткий совет (1-2 предложения) "
            f"на русском языке, без воды.\n"
            f"Психологический счёт: {signals.get('psychology_score')}/100\n"
            f"Флаги: {', '.join(signals.get('flags', []))}\n"
            f"Метрики: {signals.get('metrics', {})}\n\n"
            "Совет:"
        )
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self.provider.generate, prompt)
            return result.strip()
        except Exception:
            return ""