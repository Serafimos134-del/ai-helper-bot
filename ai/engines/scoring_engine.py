"""
ai/engines/scoring_engine.py
Deterministic scoring without LLM.
Calculates risk, psychology, data quality, confidence, and disagreement.
"""

from typing import Dict, Any

class ScoringEngine:
    """Считает метрики для позиций/сделок на основе правил."""

    def calculate(self, obj: Dict[str, Any], mode: str = 'open', market_regime: str = None) -> Dict[str, float]:
        """
        Возвращает словарь с ключами:
        risk_score, psychology_score, data_quality, confidence, disagreement
        """
        risk_score = self._calc_risk(obj)
        psych_score = self._calc_psychology(obj)
        data_quality = self._calc_data_quality(obj)

        # ── Реалистичная Confidence ─────────────────────────────────
        # 1. Data quality (чем полнее данные, тем выше)
        quality_factor = data_quality

        # 2. Signal alignment (обратный к disagreement, пока запасной)
        #    Сейчас задаётся извне, но для scorer оставим заглушку
        alignment_factor = 0.85   # 85% согласованности по умолчанию (позже будет передаваться из консенсуса)

        # 3. Market clarity – чем понятнее рынок, тем выше уверенность
        clarity_map = {
            "TRENDING_UP": 0.9,
            "TRENDING_DOWN": 0.9,
            "BULLISH": 0.85,
            "BEARISH": 0.85,
            "SIDEWAYS": 0.65,
            "RANGING": 0.55,
            "UNKNOWN": 0.4,
        }
        market_clarity = clarity_map.get(market_regime, 0.5)

        # Итоговая уверенность: взвешенная сумма трёх компонент
        confidence = (
            0.4 * quality_factor +
            0.3 * alignment_factor +
            0.3 * market_clarity
        )
        confidence = max(0.15, min(0.95, confidence))   # не даём опуститься ниже 15% и выше 95%

        # ── Реалистичный Disagreement ───────────────────────────────
        # Даже при полном согласии агентов минимальный шум 5-10%
        base_disagreement = 0.07   # 7% базовый шум
        # Если данных мало или рынок неясен, разногласия выше
        if market_regime in ("UNKNOWN", "RANGING", "SIDEWAYS"):
            base_disagreement += 0.05
        if not obj.get("stop_loss") or not obj.get("take_profit"):
            base_disagreement += 0.03   # отсутствие SL/TP увеличивает неопределённость

        disagreement = base_disagreement

        return {
            "risk_score": round(risk_score),
            "psychology_score": round(psych_score),
            "data_quality": round(data_quality, 2),
            "confidence": round(confidence, 2),
            "disagreement": round(disagreement, 2),
        }

    def _calc_risk(self, obj: dict) -> float:
        """Оценка риска конкретной позиции/сделки (0-100, где 100 = максимальный риск)."""
        score = 50.0  # нейтральный старт

        # 1. Наличие SL
        if not obj.get("stop_loss"):
            score += 25  # отсутствие стопа — большой риск
        # 2. Наличие TP
        if not obj.get("take_profit"):
            score += 10
        # 3. Кредитное плечо
        leverage = float(obj.get("leverage", 1))
        if leverage >= 10:
            score += 15
        elif leverage >= 5:
            score += 10
        elif leverage >= 3:
            score += 5
        # 4. Размер позиции относительно депозита (если есть current_price и size)
        # Для простоты не вычисляем, можно добавить позже.
        return max(0, min(100, score))

    def _calc_psychology(self, obj: dict) -> float:
        """Оценка дисциплины трейдера по данной позиции (0-100, где 100 = идеально)."""
        score = 70.0  # базовый уровень
        # Штрафы за отсутствие защитных ордеров
        if not obj.get("stop_loss"):
            score -= 25
        if not obj.get("take_profit"):
            score -= 15
        # Штраф за высокое плечо
        leverage = float(obj.get("leverage", 1))
        if leverage >= 10:
            score -= 15
        elif leverage >= 5:
            score -= 10
        return max(0, min(100, score))

    def _calc_data_quality(self, obj: dict) -> float:
        """Полнота данных: 1.0 = все критичные поля заполнены."""
        score = 0.6  # база
        if obj.get("entry_price"):
            score += 0.1
        if obj.get("stop_loss"):
            score += 0.1
        if obj.get("take_profit"):
            score += 0.1
        if obj.get("leverage"):
            score += 0.1
        return min(1.0, score)