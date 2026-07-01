"""
ai/engines/normalizer.py
Приводит объекты позиций/сделок к канонической форме для всех агентов.
"""

from typing import Dict, Any

def normalize_position(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Открытая позиция из API BingX или из контекста."""
    return {
        "symbol": raw.get("symbol", ""),
        "side": raw.get("side", "LONG"),
        "entry_price": float(raw.get("entryPrice", raw.get("entry_price", 0))),
        "current_price": float(raw.get("markPrice", raw.get("current_price", 0))),
        "unrealized_pnl": float(raw.get("unrealizedPnl", raw.get("unrealized_pnl", 0))),
        "leverage": float(raw.get("leverage", 1)),
        "stop_loss": raw.get("stopLoss") or raw.get("stop_loss"),
        "take_profit": raw.get("takeProfit") or raw.get("take_profit"),
        "size": abs(float(raw.get("positionAmt", raw.get("size", raw.get("quantity", 0))))),
    }

def normalize_trade(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Закрытая сделка из БД."""
    return {
        "symbol": raw.get("symbol", ""),
        "side": raw.get("side", ""),
        "entry_price": float(raw.get("entry_price", 0)),
        "exit_price": float(raw.get("exit_price", 0)),
        "realized_pnl": float(raw.get("realized_pnl", 0)),
        "quantity": float(raw.get("quantity", 0)),
        "leverage": float(raw.get("leverage", 1)),
        "stop_loss": raw.get("stop_loss"),
        "take_profit": raw.get("take_profit"),
        "holding_minutes": raw.get("holding_minutes"),
        "entry_comment": raw.get("entry_comment", ""),
        "exit_comment": raw.get("exit_comment", raw.get("comment", "")),
    }