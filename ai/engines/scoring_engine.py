"""
ai/engines/scoring_engine.py
Deterministic scoring without LLM.
Calculates risk, psychology, data quality, confidence, and disagreement.
"""

from typing import Dict, Any

class ScoringEngine:
    """Считает метрики для позиций/сделок на основе правил."""

    def calculate(self, obj: Dict[str, Any], mode: str = 'open') -> Dict[str, float]:
        """
        Возвращает словарь с ключами:
        risk_score, psychology_score, data_quality, confidence, disagreement
        """
        risk_score = self._calc_risk(obj)
        psych_score = self._calc_psychology(obj)
        data_quality = self._calc_data_quality(obj)
        # Confidence = среднее от качества данных и "стабильности" сигналов (пока упрощённо)
        confidence = data_quality * 0.7 + 0.3 * (1.0 - abs(risk_score - 50) / 100.0)
        # Disagreement пока фиксированный, т.к. нет других агентов (будет пересчитываться в консенсусе)
        disagreement = 0.0
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