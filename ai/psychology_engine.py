import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class PsychologyEngine:
    """Rule‑based движок для анализа психологических паттернов трейдера."""

    # Пороговые значения
    LOSING_STREAK_WARN = 2
    LOSING_STREAK_HIGH = 4
    OVERTRADE_PER_HOUR = 5
    WINRATE_DROP = 10  # процентных пунктов

    # Веса для компонентов психологического скора
    WEIGHTS = {
        "revenge": 0.35,
        "overtrade": 0.25,
        "tilt": 0.25,
        "fomo": 0.15,
    }

    @classmethod
    def assess(cls, history: Dict) -> Dict:
        """
        Анализирует историю сделок и возвращает психологический профиль.
        Возвращает структуру: {psychology_score, flags, confidence, summary}
        """
        stats = history.get("stats") or {}
        recent_trades = history.get("recent_trades", [])
        losing_streak = history.get("losing_streak", 0)
        winning_streak = history.get("winning_streak", 0)

        if not recent_trades:
            return {
                "psychology_score": 50,
                "flags": [],
                "confidence": 30,
                "summary": "Недостаточно данных для анализа психологии.",
                "metrics": {},
            }

        # 1. Revenge trading detection
        revenge_score = 0
        if losing_streak >= cls.LOSING_STREAK_HIGH:
            revenge_score = 90
        elif losing_streak >= cls.LOSING_STREAK_WARN:
            revenge_score = 60
        elif losing_streak >= 1:
            revenge_score = 30

        # Проверяем, растёт ли плечо при серии убытков
        if losing_streak >= cls.LOSING_STREAK_WARN and len(recent_trades) >= losing_streak:
            pre_streak = recent_trades[losing_streak:losing_streak+3] if len(recent_trades) > losing_streak else []
            streak_trades = recent_trades[:losing_streak]
            if pre_streak and streak_trades:
                avg_pre = sum(t.get("leverage", 1) for t in pre_streak) / len(pre_streak)
                avg_streak = sum(t.get("leverage", 1) for t in streak_trades) / len(streak_trades)
                if avg_streak > avg_pre * 1.3:
                    revenge_score = min(100, revenge_score + 20)

        # 2. Overtrade detection
        overtrade_score = 0
        if len(recent_trades) >= cls.OVERTRADE_PER_HOUR:
            # Смотрим на время последних N сделок
            try:
                # Упрощённо: если много сделок в последних записях, считаем это overtrading
                overtrade_score = min(100, len(recent_trades) * 5)
            except Exception:
                overtrade_score = 30
        else:
            overtrade_score = max(0, 100 - len(recent_trades) * 10)

        # 3. Tilt detection
        tilt_score = 0
        win_rate = stats.get("win_rate", 50)
        if win_rate < 40:
            tilt_score = 70
        elif win_rate < 50:
            tilt_score = 40
        else:
            tilt_score = max(0, 30 - losing_streak * 10)

        # Если Win Rate упал более чем на WINRATE_DROP п.п. за последние сделки
        if len(recent_trades) >= 5:
            recent_wins = sum(1 for t in recent_trades[:10] if t.get("pnl", 0) > 0)
            recent_winrate = (recent_wins / min(10, len(recent_trades))) * 100
            if recent_winrate < win_rate - cls.WINRATE_DROP:
                tilt_score = min(100, tilt_score + 20)

        # 4. FOMO detection
        fomo_score = 0
        if losing_streak >= 1:
            # Проверяем, входит ли трейдер сразу после убытка без комментария
            no_comment_trades = sum(1 for t in recent_trades[:losing_streak] if not t.get("comment"))
            if no_comment_trades > 0:
                fomo_score = min(100, no_comment_trades * 15)
        # Если после серии прибылей следует большая сделка — тоже FOMO
        if winning_streak >= 3 and len(recent_trades) > winning_streak:
            first_after = recent_trades[winning_streak]
            if first_after.get("pnl", 0) < 0:
                fomo_score = min(100, fomo_score + 30)

        # 5. Взвешенный психологический score
        raw_score = (
            revenge_score * cls.WEIGHTS["revenge"] +
            overtrade_score * cls.WEIGHTS["overtrade"] +
            tilt_score * cls.WEIGHTS["tilt"] +
            fomo_score * cls.WEIGHTS["fomo"]
        )
        # Нормализуем к 0–100 (где 100 = отличное психологическое состояние)
        psychology_score = 100 - int(max(0, min(100, raw_score)))

        # 6. Flags
        flags = []
        if revenge_score >= 60:
            flags.append("revenge_trading")
        if revenge_score >= 80:
            flags.append("severe_revenge_trading")
        if overtrade_score >= 50:
            flags.append("overtrading")
        if tilt_score >= 60:
            flags.append("tilt")
        if fomo_score >= 50:
            flags.append("fomo")

        # 7. Confidence
        confidence = 80
        if len(recent_trades) < 5:
            confidence = 40
        elif len(recent_trades) < 10:
            confidence = 60

        # 8. Summary (без LLM)
        summary = cls._generate_summary(psychology_score, flags)

        # 9. Metrics
        metrics = {
            "revenge_score": revenge_score,
            "overtrade_score": overtrade_score,
            "tilt_score": tilt_score,
            "fomo_score": fomo_score,
            "losing_streak": losing_streak,
            "winning_streak": winning_streak,
            "total_trades_analyzed": len(recent_trades),
        }

        return {
            "psychology_score": psychology_score,
            "flags": flags,
            "confidence": confidence,
            "summary": summary,
            "metrics": metrics,
        }

    @classmethod
    def _generate_summary(cls, score: int, flags: List[str]) -> str:
        """Генерирует текстовое summary на основе флагов."""
        if score >= 80:
            base = "Психологическое состояние отличное. Трейдер действует дисциплинированно."
        elif score >= 60:
            base = "Психологическое состояние нормальное. Есть незначительные отклонения."
        elif score >= 40:
            base = "Психологическое состояние напряжённое. Требуется внимание к дисциплине."
        else:
            base = "Психологическое состояние плохое. Высок риск эмоциональных решений."

        if "revenge_trading" in flags:
            base += " Обнаружен паттерн revenge trading."
        if "tilt" in flags:
            base += " Признаки тильта."
        if "overtrading" in flags:
            base += " Замечен overtrading."
        if "fomo" in flags:
            base += " Возможен FOMO."

        return base