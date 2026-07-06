"""
utils/liquidation.py
Единая формула оценки цены ликвидации (isolated margin) и классификации
волатильности символа — раньше была продублирована с небольшими расхождениями
в services/calc_engine.py и ai/risk_engine.py, из-за чего /calc и портфельная
оценка риска показывали разные числа для одной и той же позиции.

Это упрощённая оценка (без учёта комиссий и точной формулы биржи) — перед тем
как полностью доверять этим цифрам, стоит сверить их с реальной документацией/
поведением BingX.
"""

LOW_VOLATILITY = {"BTC-USDT", "ETH-USDT"}
MEDIUM_VOLATILITY = {"SOL-USDT", "BNB-USDT", "XRP-USDT", "ADA-USDT"}


def get_volatility_class(symbol: str) -> str:
    if symbol in LOW_VOLATILITY:
        return "LOW"
    if symbol in MEDIUM_VOLATILITY:
        return "MEDIUM"
    return "HIGH"


def get_mmr(symbol: str) -> float:
    """Maintenance margin rate по символу: BTC/ETH — 0.5%, популярные альты — 1%, остальные — 2%."""
    if symbol in LOW_VOLATILITY:
        return 0.005
    if symbol in MEDIUM_VOLATILITY:
        return 0.01
    return 0.02


def estimate_liquidation_price(entry: float, leverage: float, side: str, symbol: str = "") -> float:
    """
    Оценка цены ликвидации при изолированной марже.
      LONG:  liq = entry * (1 - (1 - mmr) / leverage)
      SHORT: liq = entry * (1 + (1 - mmr) / leverage)
    """
    if leverage <= 0:
        leverage = 1
    mmr = get_mmr(symbol)
    margin_factor = (1.0 - mmr) / leverage
    if side == "LONG":
        return entry * (1.0 - margin_factor)
    else:
        return entry * (1.0 + margin_factor)
