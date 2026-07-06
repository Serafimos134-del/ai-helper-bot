"""
utils/formatting.py
Общие функции форматирования вывода для Telegram-сообщений.
Раньше format_verdict была продублирована дословно в core/router.py и
services/auto_sync.py — вынесена сюда, чтобы у обоих был один источник правды.
"""

import json


def format_verdict(verdict_raw) -> str:
    """Форматирует JSON-вердикт JudgeAgent в читаемую строку с эмодзи."""
    try:
        verdict = json.loads(verdict_raw) if isinstance(verdict_raw, str) else verdict_raw
        verdict_text    = verdict.get('verdict', '—')
        final_score     = verdict.get('final_score', '—')
        verdict_summary = verdict.get('summary', '')
        warnings        = verdict.get('warnings', [])
        emoji_map = {'STRONG_ENTER': '🟢', 'ENTER': '🟢', 'WAIT': '🟡', 'AVOID': '🔴'}
        emoji = emoji_map.get(verdict_text, '⚪')
        result = f"{emoji} {verdict_text} ({final_score}/100)"
        if verdict_summary:
            result += f"\n{verdict_summary}"
        if warnings:
            result += "\n⚠️ " + " | ".join(warnings)
        return result
    except Exception:
        return str(verdict_raw)
