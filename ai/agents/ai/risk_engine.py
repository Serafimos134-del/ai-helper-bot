import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class RiskRuleEngine:
    """Рассчитывает риск-метрики без использования LLM."""

    # Пороговые значения
    RISK_PER_TRADE_WARN = 2.0       # % от депозита
    RISK_PER_TRADE_HIGH = 3.0
    LEVERAGE_WARN = 20
    LEVERAGE_HIGH = 30
    EXPOSURE_WARN = 50.0
    EXPOSURE_HIGH = 75.0
    LOSING_STREAK_WARN = 3
    DAILY_PNL_HIGH = -5.0           # %
    RR_BAD = 1.5
    DRAWDOWN_CAUTION = 5.0          # %
    DRAWDOWN_DEFENSIVE = 10.0
    DRAWDOWN_CRITICAL = 15.0

    # Классификация волатильности активов (упрощённо)
    LOW_VOLATILITY = {"BTC-USDT", "ETH-USDT"}
    MEDIUM_VOLATILITY = {"SOL-USDT", "BNB-USDT", "XRP-USDT", "ADA-USDT"}
    # всё остальное — высокая волатильность

    @staticmethod
    def _get_volatility_class(symbol: str) -> str:
        if symbol in RiskRuleEngine.LOW_VOLATILITY:
            return "LOW"
        if symbol in RiskRuleEngine.MEDIUM_VOLATILITY:
            return "MEDIUM"
        return "HIGH"

    @staticmethod
    def assess(portfolio: Dict, history: Dict) -> Dict:
        """
        Основной метод оценки риска.

        Args:
            portfolio: данные портфеля из ContextBuilder
            history: исторические данные из ContextBuilder

        Returns:
            Словарь с сигналами риска
        """
        balance = portfolio.get("balance") or 0
        used_margin = portfolio.get("used_margin") or 0
        unrealized_pnl = portfolio.get("unrealized_pnl") or 0
        open_positions = portfolio.get("open_positions", [])
        pos_count = len(open_positions)
        losing_streak = history.get("losing_streak", 0)

        if balance <= 0:
            return {"risk_level": "UNKNOWN", "risk_score": 0, "warnings": ["Нет данных о балансе"]}

        # 1. Exposure
        exposure_pct = (used_margin / balance) * 100

        # 2. Риск на позицию (средняя маржа на позицию / баланс)
        avg_margin_per_pos = used_margin / pos_count if pos_count > 0 else 0
        risk_per_trade_pct = (avg_margin_per_pos / balance) * 100 if balance > 0 else 0

        # 3. Максимальное плечо
        max_leverage = max((p.get("leverage", 1) for p in open_positions), default=1)

        # 4. R:R из статистики
        stats = history.get("stats") or {}
        avg_profit = abs(stats.get("avg_profit", 0))
        avg_loss = abs(stats.get("avg_loss", 0))
        rr = (avg_profit / avg_loss) if avg_loss > 0 else 999

        # 5. Дневной PnL % (упрощённо)
        daily_pnl_pct = (unrealized_pnl / balance) * 100

        # 6. Volatility risk
        high_vol_count = 0
        for p in open_positions:
            symbol = p.get("symbol", "")
            if RiskRuleEngine._get_volatility_class(symbol) == "HIGH":
                high_vol_count += 1
        volatility_risk = "HIGH" if high_vol_count > 0 and max_leverage >= 15 else "LOW"

        # 7. Correlation risk
        long_count = sum(1 for p in open_positions if p.get("side") == "LONG")
        short_count = sum(1 for p in open_positions if p.get("side") == "SHORT")
        correlation_risk = "HIGH" if (long_count >= 2 or short_count >= 2) else "LOW"

        # 8. Drawdown mode (приблизительно по текущему PnL)
        drawdown_pct = abs(daily_pnl_pct) if daily_pnl_pct < 0 else 0
        if drawdown_pct > RiskRuleEngine.DRAWDOWN_CRITICAL:
            drawdown_mode = "CRITICAL"
        elif drawdown_pct > RiskRuleEngine.DRAWDOWN_DEFENSIVE:
            drawdown_mode = "DEFENSIVE"
        elif drawdown_pct > RiskRuleEngine.DRAWDOWN_CAUTION:
            drawdown_mode = "CAUTION"
        else:
            drawdown_mode = "NORMAL"

        # 9. Применяем правила и собираем warnings
        warnings = []
        risk_level = "SAFE"
        risk_score = 1

        def escalate(new_level: str, new_score: int):
            nonlocal risk_level, risk_score
            level_order = {"SAFE": 0, "MODERATE": 1, "HIGH": 2, "EXTREME": 3}
            if level_order[new_level] > level_order[risk_level]:
                risk_level = new_level
            risk_score = max(risk_score, new_score)

        # Risk per trade
        if risk_per_trade_pct >= RiskRuleEngine.RISK_PER_TRADE_HIGH:
            warnings.append(f"Риск на сделку: {risk_per_trade_pct:.1f}% депозита (критический)")
            escalate("HIGH", 7)
        elif risk_per_trade_pct >= RiskRuleEngine.RISK_PER_TRADE_WARN:
            warnings.append(f"Риск на сделку: {risk_per_trade_pct:.1f}% депозита (повышен)")
            escalate("MODERATE", 5)

        # Leverage
        if max_leverage >= RiskRuleEngine.LEVERAGE_HIGH:
            warnings.append(f"Плечо {max_leverage}x (экстремальное)")
            escalate("EXTREME", 9)
        elif max_leverage >= RiskRuleEngine.LEVERAGE_WARN:
            warnings.append(f"Плечо {max_leverage}x (высокое)")
            escalate("HIGH", 7)

        # Exposure
        if exposure_pct >= RiskRuleEngine.EXPOSURE_HIGH:
            warnings.append(f"Загрузка депозита {exposure_pct:.1f}% (критическая)")
            escalate("EXTREME", 9)
        elif exposure_pct >= RiskRuleEngine.EXPOSURE_WARN:
            warnings.append(f"Загрузка депозита {exposure_pct:.1f}% (высокая)")
            escalate("HIGH", 6)

        # Losing streak
        if losing_streak >= RiskRuleEngine.LOSING_STREAK_WARN:
            warnings.append(f"Серия убытков: {losing_streak} подряд")
            escalate("MODERATE", 6)

        # Revenge trading паттерн
        if losing_streak >= RiskRuleEngine.LOSING_STREAK_WARN and max_leverage > 15:
            warnings.append("⚠️ Паттерн revenge trading (серия убытков + высокое плечо)")
            escalate("EXTREME" if risk_level in ("HIGH", "EXTREME") else "HIGH", 8)

        # Daily PnL
        if daily_pnl_pct <= RiskRuleEngine.DAILY_PNL_HIGH:
            warnings.append(f"Просадка за день: {daily_pnl_pct:.1f}%")
            escalate("HIGH", 8)

        # R:R
        if rr < RiskRuleEngine.RR_BAD:
            warnings.append(f"R:R {rr:.2f} (низкий)")
            escalate("MODERATE", 5)

        # Volatility risk
        if volatility_risk == "HIGH":
            warnings.append(f"Высокая волатильность активов при плече ≥15x")
            escalate("HIGH", 7)

        # Correlation risk
        if correlation_risk == "HIGH":
            warnings.append(f"Обнаружена высокая корреляция позиций ({long_count} LONG / {short_count} SHORT)")
            escalate("HIGH", 7)

        # Drawdown mode усиливает риск
        if drawdown_mode == "CRITICAL":
            warnings.append("Критическая просадка депозита")
            escalate("EXTREME", 10)
        elif drawdown_mode == "DEFENSIVE":
            warnings.append("Значительная просадка — повышенная осторожность")
            escalate("HIGH", 8)
        elif drawdown_mode == "CAUTION":
            warnings.append("Просадка выше нормы — будь внимателен")
            escalate("MODERATE", 6)

        # Рекомендация
        if risk_level == "EXTREME":
            recommendation = "CLOSE_POSITIONS"
        elif risk_level == "HIGH":
            recommendation = "AVOID_NEW_TRADES"
        elif risk_level == "MODERATE":
            recommendation = "REDUCE_SIZE"
        else:
            recommendation = "ALLOW"

        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "exposure_pct": round(exposure_pct, 1),
            "max_leverage": max_leverage,
            "risk_per_trade_pct": round(risk_per_trade_pct, 1),
            "rr_ratio": round(rr, 2),
            "losing_streak": losing_streak,
            "volatility_risk": volatility_risk,
            "correlation_risk": correlation_risk,
            "drawdown_mode": drawdown_mode,
            "drawdown_pct": round(drawdown_pct, 1),
            "warnings": warnings,
            "recommendation": recommendation,
        }