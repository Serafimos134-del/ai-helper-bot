import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class TradeScorer:
    """Оценивает закрытую сделку по 10-балльной шкале на основе риск-метрик."""

    # Веса для компонентов оценки
    WEIGHTS = {
        "rr_ratio": 0.30,         # Соотношение риск/прибыль
        "leverage": 0.25,          # Плечо
        "risk_per_trade": 0.20,    # Риск на сделку
        "discipline": 0.15,        # Дисциплина (комментарий, стоп-лосс)
        "psychology": 0.10,        # Психология (серия убытков, длительность)
    }

    @staticmethod
    def score(trade: Dict, context: Optional[Dict] = None) -> Dict:
        """
        Оценивает сделку и возвращает словарь с оценками.

        Args:
            trade: словарь с полями закрытой сделки (symbol, side, entry_price,
                   exit_price, realized_pnl, leverage, stop_loss, take_profit,
                   exit_comment, holding_minutes)
            context: дополнительные данные (losing_streak, balance) – опционально

        Returns:
            Словарь с итоговой оценкой и детализацией
        """
        scores = {}

        # 1. Risk/Reward (R:R)
        entry = float(trade.get("entry_price", 0))
        exit_p = float(trade.get("exit_price", 0))
        stop_loss = trade.get("stop_loss")
        take_profit = trade.get("take_profit")

        if stop_loss and entry and exit_p:
            risk = abs(entry - float(stop_loss))
            reward = abs(exit_p - entry)
            rr = reward / risk if risk > 0 else 0
            if rr >= 3:
                scores["rr_ratio"] = 10
            elif rr >= 2:
                scores["rr_ratio"] = 8
            elif rr >= 1.5:
                scores["rr_ratio"] = 6
            elif rr >= 1:
                scores["rr_ratio"] = 4
            else:
                scores["rr_ratio"] = 2
        elif take_profit and stop_loss:
            # Есть и стоп, и тейк — дисциплина есть, но без точного R:R
            scores["rr_ratio"] = 5
        else:
            scores["rr_ratio"] = 3  # Нет стоп-лосса — плохо

        # 2. Плечо
        leverage = float(trade.get("leverage", 1))
        if leverage <= 5:
            scores["leverage"] = 10
        elif leverage <= 10:
            scores["leverage"] = 8
        elif leverage <= 20:
            scores["leverage"] = 5
        elif leverage <= 30:
            scores["leverage"] = 3
        else:
            scores["leverage"] = 1

        # 3. Риск на сделку (% от депозита)
        balance = (context or {}).get("balance", 0)
        quantity = float(trade.get("quantity", 0))
        if balance > 0 and quantity > 0 and entry > 0:
            risk_amount = (quantity * entry) / leverage
            risk_pct = (risk_amount / balance) * 100
            if risk_pct <= 1:
                scores["risk_per_trade"] = 10
            elif risk_pct <= 2:
                scores["risk_per_trade"] = 8
            elif risk_pct <= 3:
                scores["risk_per_trade"] = 6
            elif risk_pct <= 5:
                scores["risk_per_trade"] = 4
            else:
                scores["risk_per_trade"] = 2
        else:
            scores["risk_per_trade"] = 5  # Нет данных — нейтрально

        # 4. Дисциплина (наличие комментария и стоп-лосса)
        discipline = 0
        if trade.get("exit_comment"):
            discipline += 5  # Есть вывод по сделке
        if stop_loss:
            discipline += 3  # Был установлен стоп-лосс
        if take_profit:
            discipline += 2  # Был установлен тейк-профит
        scores["discipline"] = min(10, discipline)

        # 5. Психология (серия убытков, длительность удержания)
        psychology = 5  # Базовый уровень
        losing_streak = (context or {}).get("losing_streak", 0)
        holding_minutes = trade.get("holding_minutes")

        if losing_streak >= 3:
            psychology -= 2  # Серия убытков — возможен revenge trading

        if holding_minutes is not None:
            if holding_minutes < 1:
                psychology -= 2  # Слишком быстрая сделка — импульсивность
            elif holding_minutes > 60:
                psychology += 2  # Долгое удержание — терпение

        pnl = float(trade.get("realized_pnl", 0))
        if pnl < 0 and losing_streak >= 2:
            psychology -= 2  # Убыток в серии — эмоциональное давление

        if pnl > 0 and losing_streak == 0:
            psychology += 1  # Прибыль без давления — хороший знак

        scores["psychology"] = max(1, min(10, psychology))

        # Итоговая взвешенная оценка
        total = sum(
            scores[key] * TradeScorer.WEIGHTS[key]
            for key in TradeScorer.WEIGHTS
            if key in scores
        )

        return {
            "total_score": round(total, 1),
            "details": scores,
            "verdict": TradeScorer._verdict(total),
        }

    @staticmethod
    def _verdict(score: float) -> str:
        if score >= 8:
            return "Отличная сделка"
        elif score >= 6:
            return "Хорошая сделка"
        elif score >= 4:
            return "Средняя сделка"
        elif score >= 2:
            return "Плохая сделка"
        else:
            return "Ужасная сделка"