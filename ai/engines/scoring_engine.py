"""
ai/engines/scoring_engine.py
Deterministic scoring without LLM.
Calculates risk, psychology, data quality, confidence, and disagreement.
"""

from typing import Dict, Any

class ScoringEngine:
    """Считает метрики для позиций/сделок на основе правил."""

    def calculate(self, obj: Dict[str, Any], mode: str = 'open', market_regime: str = None) -> Dict[str, float]:
        # ── Для сетапов нейтральные метрики, без штрафов ──
        if mode == 'setup':
            risk_score = 50
            if market_regime in ("TRENDING_UP", "TRENDING_DOWN"):
                risk_score = 60
            elif market_regime in ("RANGING", "SIDEWAYS"):
                risk_score = 55
            return {
                "risk_score": risk_score,
                "psychology_score": 70,   # нейтральный, агент сам оценит
                "data_quality": 0.6,
                "confidence": 0.75,
                "disagreement": 0.1,
            }

        # ── Стандартный расчёт для open/post_trade ──
        risk_score = self.calc_risk(obj)
        psych_score = self.calc_psychology(obj)
        data_quality = self.calc_data_quality(obj)

        quality_factor = data_quality
        alignment_factor = 0.85
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

        confidence = (
            0.4 * quality_factor +
            0.3 * alignment_factor +
            0.3 * market_clarity
        )
        confidence = max(0.15, min(0.95, confidence))

        base_disagreement = 0.07
        if market_regime in ("UNKNOWN", "RANGING", "SIDEWAYS"):
            base_disagreement += 0.05
        if not obj.get("stop_loss") or not obj.get("take_profit"):
            base_disagreement += 0.03

        disagreement = base_disagreement

        return {
            "risk_score": round(risk_score),
            "psychology_score": round(psych_score),
            "data_quality": round(data_quality, 2),
            "confidence": round(confidence, 2),
            "disagreement": round(disagreement, 2),
        }

    def calc_risk(self, obj: dict) -> float:
        """Скор безопасности позиции: ВЫШЕ = БЕЗОПАСНЕЕ (есть SL/TP, разумное плечо).
        Шкала 0-100 — канонична для всего AI Trading Core: JudgeAgent, warnings-проверки
        и RiskAgent.analyze() (open/post_trade) используют именно эту функцию как единый
        источник правды, чтобы не поддерживать копию той же логики в другой шкале
        (см. AUDIT.md, находка про 0-10 vs 0-100). Раньше здесь была обратная шкала
        (выше = опаснее), из-за чего опасные позиции завышали итоговый score вместо
        того, чтобы его понижать."""
        score = 100.0
        if not obj.get("stop_loss"):
            score -= 25
        if not obj.get("take_profit"):
            score -= 10
        leverage = float(obj.get("leverage", 1))
        if leverage >= 10:
            score -= 15
        elif leverage >= 5:
            score -= 10
        elif leverage >= 3:
            score -= 5
        return max(0, min(100, score))

    def calc_psychology(self, obj: dict) -> float:
        score = 70.0
        if not obj.get("stop_loss"):
            score -= 25
        if not obj.get("take_profit"):
            score -= 15
        leverage = float(obj.get("leverage", 1))
        if leverage >= 10:
            score -= 15
        elif leverage >= 5:
            score -= 10
        return max(0, min(100, score))

    def calc_data_quality(self, obj: dict) -> float:
        score = 0.6
        if obj.get("entry_price"):
            score += 0.1
        if obj.get("stop_loss"):
            score += 0.1
        if obj.get("take_profit"):
            score += 0.1
        if obj.get("leverage"):
            score += 0.1
        return min(1.0, score)