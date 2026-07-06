import logging
from typing import Dict, List, Tuple

from utils.liquidation import (
    LOW_VOLATILITY,
    MEDIUM_VOLATILITY,
    get_volatility_class,
    estimate_liquidation_price,
)

logger = logging.getLogger(__name__)


class RiskRuleEngine:
    """Рассчитывает риск-метрики без использования LLM."""

    RISK_PER_TRADE_WARN = 2.0
    RISK_PER_TRADE_HIGH = 3.0
    LEVERAGE_WARN = 20
    LEVERAGE_HIGH = 30
    EXPOSURE_WARN = 100.0
    EXPOSURE_HIGH = 150.0
    LOSING_STREAK_WARN = 3
    DAILY_PNL_HIGH = -5.0
    RR_BAD = 1.5
    DRAWDOWN_CAUTION = 5.0
    DRAWDOWN_DEFENSIVE = 10.0
    DRAWDOWN_CRITICAL = 15.0
    LIQUIDATION_WARN = 5.0
    LIQUIDATION_CRITICAL = 2.0

    # Классификация волатильности и оценка ликвидации вынесены в
    # utils/liquidation.py — общий модуль, используемый также в calc_engine.py,
    # чтобы /calc и портфельная оценка риска не расходились в цифрах.
    LOW_VOLATILITY = LOW_VOLATILITY
    MEDIUM_VOLATILITY = MEDIUM_VOLATILITY

    _get_volatility_class = staticmethod(get_volatility_class)
    _estimate_liquidation_price = staticmethod(estimate_liquidation_price)

    @staticmethod
    def assess(portfolio: Dict, history: Dict) -> Dict:
        balance = portfolio.get("balance") or 0
        used_margin = portfolio.get("used_margin") or 0
        unrealized_pnl = portfolio.get("unrealized_pnl") or 0
        open_positions = portfolio.get("open_positions", [])
        pos_count = len(open_positions)
        losing_streak = history.get("losing_streak", 0)

        if balance <= 0:
            return {"risk_level": "UNKNOWN", "risk_score": 0, "warnings": ["Нет данных о балансе"]}

        position_notional = sum(
            float(p.get("entry_price", 0)) * abs(float(p.get("size", p.get("quantity", 0))))
            for p in open_positions
        )
        exposure_pct = (position_notional / balance) * 100 if balance > 0 else 0
        margin_usage_pct = (used_margin / balance) * 100 if balance > 0 else 0

        total_risk = 0.0
        max_individual_risk = 0.0
        for p in open_positions:
            entry = float(p.get("entry_price", 0))
            qty = abs(float(p.get("size", p.get("quantity", 0))))
            stop_loss = p.get("stop_loss")
            if stop_loss and entry > 0:
                risk = abs(entry - float(stop_loss)) * qty
            elif entry > 0:
                risk = 0.02 * entry * qty
            else:
                risk = 0.0
            total_risk += risk
            if risk > max_individual_risk:
                max_individual_risk = risk

        risk_per_trade_pct = (total_risk / balance) * 100 if balance > 0 else 0
        max_individual_risk_pct = (max_individual_risk / balance) * 100 if balance > 0 else 0

        max_leverage = max((p.get("leverage", 1) for p in open_positions), default=1)

        stats = history.get("stats") or {}
        avg_profit = abs(stats.get("avg_profit", 0))
        avg_loss = abs(stats.get("avg_loss", 0))
        rr = (avg_profit / avg_loss) if avg_loss > 0 else 999

        daily_pnl_pct = (unrealized_pnl / balance) * 100

        high_vol_count = 0
        for p in open_positions:
            symbol = p.get("symbol", "")
            if RiskRuleEngine._get_volatility_class(symbol) == "HIGH":
                high_vol_count += 1
        volatility_risk = "HIGH" if high_vol_count > 0 and max_leverage >= 15 else "LOW"

        long_count = sum(1 for p in open_positions if p.get("side") == "LONG")
        short_count = sum(1 for p in open_positions if p.get("side") == "SHORT")
        correlation_risk = "HIGH" if (long_count >= 2 or short_count >= 2) else "LOW"

        drawdown_pct = abs(daily_pnl_pct) if daily_pnl_pct < 0 else 0
        if drawdown_pct > RiskRuleEngine.DRAWDOWN_CRITICAL:
            drawdown_mode = "CRITICAL"
        elif drawdown_pct > RiskRuleEngine.DRAWDOWN_DEFENSIVE:
            drawdown_mode = "DEFENSIVE"
        elif drawdown_pct > RiskRuleEngine.DRAWDOWN_CAUTION:
            drawdown_mode = "CAUTION"
        else:
            drawdown_mode = "NORMAL"

        min_liq_distance = 100.0
        liquidation_warnings = []
        for p in open_positions:
            entry = float(p.get("entry_price", 0))
            leverage = float(p.get("leverage", 1))
            side = p.get("side", "LONG")
            symbol = p.get("symbol", "")
            if entry > 0 and leverage > 1:
                liq_price = RiskRuleEngine._estimate_liquidation_price(entry, leverage, side, symbol)
                current_price = float(p.get("current_price", entry))
                if current_price > 0:
                    if side == "LONG":
                        distance_pct = ((current_price - liq_price) / current_price) * 100
                    else:
                        distance_pct = ((liq_price - current_price) / current_price) * 100
                    distance_pct = max(0.0, distance_pct)
                    if distance_pct < min_liq_distance:
                        min_liq_distance = distance_pct
                    if distance_pct < RiskRuleEngine.LIQUIDATION_CRITICAL:
                        liquidation_warnings.append(f"⚠️ {symbol}: ликвидация в {distance_pct:.1f}% (критически близко)")
                    elif distance_pct < RiskRuleEngine.LIQUIDATION_WARN:
                        liquidation_warnings.append(f"⚠️ {symbol}: ликвидация в {distance_pct:.1f}% (близко)")

        warnings = []
        risk_level = "SAFE"
        risk_score = 1

        def escalate(new_level: str, new_score: int):
            nonlocal risk_level, risk_score
            level_order = {"SAFE": 0, "MODERATE": 1, "HIGH": 2, "EXTREME": 3}
            if level_order[new_level] > level_order[risk_level]:
                risk_level = new_level
            risk_score = max(risk_score, new_score)

        if risk_per_trade_pct >= RiskRuleEngine.RISK_PER_TRADE_HIGH:
            warnings.append(f"Риск на все позиции: {risk_per_trade_pct:.1f}% депозита (критический)")
            escalate("HIGH", 8)
        elif risk_per_trade_pct >= RiskRuleEngine.RISK_PER_TRADE_WARN:
            warnings.append(f"Риск на все позиции: {risk_per_trade_pct:.1f}% депозита (повышен)")
            escalate("MODERATE", 5)

        if max_individual_risk_pct >= RiskRuleEngine.RISK_PER_TRADE_HIGH:
            warnings.append(f"Максимальный риск на позицию: {max_individual_risk_pct:.1f}% депозита")
            escalate("HIGH", 7)

        if max_leverage >= RiskRuleEngine.LEVERAGE_HIGH:
            warnings.append(f"Плечо {max_leverage}x (экстремальное)")
            escalate("EXTREME", 9)
        elif max_leverage >= RiskRuleEngine.LEVERAGE_WARN:
            warnings.append(f"Плечо {max_leverage}x (высокое)")
            escalate("HIGH", 7)

        if exposure_pct >= RiskRuleEngine.EXPOSURE_HIGH:
            warnings.append(f"Экспозиция {exposure_pct:.1f}% депозита (критическая)")
            escalate("EXTREME", 9)
        elif exposure_pct >= RiskRuleEngine.EXPOSURE_WARN:
            warnings.append(f"Экспозиция {exposure_pct:.1f}% депозита (высокая)")
            escalate("HIGH", 6)

        if losing_streak >= RiskRuleEngine.LOSING_STREAK_WARN:
            warnings.append(f"Серия убытков: {losing_streak} подряд")
            escalate("MODERATE", 6)

        if losing_streak >= RiskRuleEngine.LOSING_STREAK_WARN and max_leverage > 15:
            warnings.append("⚠️ Паттерн revenge trading (серия убытков + высокое плечо)")
            escalate("EXTREME" if risk_level in ("HIGH", "EXTREME") else "HIGH", 8)

        if daily_pnl_pct <= RiskRuleEngine.DAILY_PNL_HIGH:
            warnings.append(f"Просадка за день: {daily_pnl_pct:.1f}%")
            escalate("HIGH", 8)

        if rr < RiskRuleEngine.RR_BAD:
            warnings.append(f"R:R {rr:.2f} (низкий)")
            escalate("MODERATE", 5)

        if volatility_risk == "HIGH":
            warnings.append(f"Высокая волатильность активов при плече ≥15x")
            escalate("HIGH", 7)

        if correlation_risk == "HIGH":
            warnings.append(f"Обнаружена высокая корреляция позиций ({long_count} LONG / {short_count} SHORT)")
            escalate("HIGH", 7)

        for liq_warn in liquidation_warnings:
            warnings.append(liq_warn)
            if min_liq_distance < RiskRuleEngine.LIQUIDATION_CRITICAL:
                escalate("EXTREME", 10)
            elif min_liq_distance < RiskRuleEngine.LIQUIDATION_WARN:
                escalate("HIGH", 8)

        if drawdown_mode == "CRITICAL":
            warnings.append("Критическая просадка депозита")
            escalate("EXTREME", 10)
        elif drawdown_mode == "DEFENSIVE":
            warnings.append("Значительная просадка — повышенная осторожность")
            escalate("HIGH", 8)
        elif drawdown_mode == "CAUTION":
            warnings.append("Просадка выше нормы — будь внимателен")
            escalate("MODERATE", 6)

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
            "margin_usage_pct": round(margin_usage_pct, 1),
            "max_leverage": max_leverage,
            "risk_per_trade_pct": round(risk_per_trade_pct, 1),
            "max_individual_risk_pct": round(max_individual_risk_pct, 1),
            "rr_ratio": round(rr, 2),
            "losing_streak": losing_streak,
            "volatility_risk": volatility_risk,
            "correlation_risk": correlation_risk,
            "drawdown_mode": drawdown_mode,
            "drawdown_pct": round(drawdown_pct, 1),
            "min_liquidation_distance_pct": round(min_liq_distance, 1),
            "liquidation_warnings": liquidation_warnings,
            "warnings": warnings,
            "recommendation": recommendation,
        }