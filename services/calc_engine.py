"""
services/calc_engine.py
Position calculator — маржа, ликвидация, риск.
Поддерживает isolated и cross margin.
"""

from utils.liquidation import estimate_liquidation_price, get_mmr


def calculate_position(
    symbol: str,
    price: float,
    leverage: int,
    balance: float,
    risk_percent: float = 1.0,
    margin_type: str = 'isolated'
) -> dict:
    if price <= 0 or leverage <= 0 or balance <= 0:
        return {'error': 'Некорректные данные'}

    # risk_amount — сколько денег готовы выделить под маржу этой сделки.
    # margin = risk_amount (маржа = то, что вносим), leverage применяется
    # только один раз при переводе маржи в номинальный объём позиции.
    # (Раньше leverage применялся дважды: margin = risk_amount * leverage,
    # что завышало реальный объём позиции в leverage раз против заявленного риска.)
    risk_amount   = balance * risk_percent / 100
    margin        = risk_amount
    position_size = margin * leverage / price
    notional      = position_size * price

    # maintenance margin rate теперь берётся по символу (BTC/ETH ниже, мелкие
    # альты выше) из общего модуля utils/liquidation.py — той же функцией,
    # что использует портфельная оценка риска (ai/risk_engine.py). Раньше тут
    # была отдельная формула с фиксированным maintenance=0.5%, из-за чего /calc
    # и оценка риска портфеля показывали разную цену ликвидации для одной и
    # той же позиции.
    maintenance = get_mmr(symbol.upper())

    if margin_type == 'cross':
        # Cross: весь баланс защищает позицию
        # Ликвидация наступает когда баланс = maintenance margin
        try:
            liq_long  = (price * position_size - balance) / (position_size * (1 - maintenance))
            liq_short = (price * position_size + balance) / (position_size * (1 + maintenance))
            # Защита от отрицательной ликвидации (баланс больше позиции)
            liq_long  = max(liq_long,  price * 0.01)
            liq_short = min(liq_short, price * 10.0)
        except ZeroDivisionError:
            liq_long  = estimate_liquidation_price(price, leverage, 'LONG', symbol.upper())
            liq_short = estimate_liquidation_price(price, leverage, 'SHORT', symbol.upper())
    else:
        # Isolated: используем общую формулу (см. utils/liquidation.py)
        liq_long  = estimate_liquidation_price(price, leverage, 'LONG', symbol.upper())
        liq_short = estimate_liquidation_price(price, leverage, 'SHORT', symbol.upper())

    liq_distance_long  = round(abs(price - liq_long)  / price * 100, 2)
    liq_distance_short = round(abs(liq_short - price) / price * 100, 2)

    return {
        'symbol':               symbol.upper(),
        'price':                price,
        'leverage':             leverage,
        'balance':              balance,
        'risk_percent':         risk_percent,
        'risk_amount':          round(risk_amount, 2),
        'margin':               round(margin, 2),
        'position_size':        round(position_size, 4),
        'notional':             round(notional, 2),
        'liq_long':             round(liq_long, 4),
        'liq_short':            round(liq_short, 4),
        'liq_distance_long':    liq_distance_long,
        'liq_distance_short':   liq_distance_short,
        'margin_type':          margin_type,
    }


def format_calc_result(result: dict, side: str = None) -> str:
    if result.get('error'):
        return f"❌ {result['error']}"

    side_str     = f" {side.upper()}" if side else ""
    margin_label = "Cross ✖️" if result['margin_type'] == 'cross' else "Isolated 🔒"

    if not side or side.upper() == 'LONG':
        liq      = result['liq_long']
        liq_dist = result['liq_distance_long']
    else:
        liq      = result['liq_short']
        liq_dist = result['liq_distance_short']

    return (
        f"🧮 Калькулятор позиции\n\n"
        f"📌 {result['symbol']}{side_str} × {result['leverage']}x | {margin_label}\n"
        f"💵 Цена входа: ${result['price']:,.4f}\n\n"
        f"💰 Баланс депо: ${result['balance']:,.2f}\n"
        f"⚠️ Риск: {result['risk_percent']}% = ${result['risk_amount']:,.2f}\n\n"
        f"📦 Маржа: ${result['margin']:,.2f}\n"
        f"📊 Размер позиции: {result['position_size']} {result['symbol'].split('-')[0]}\n"
        f"💼 Объём: ${result['notional']:,.2f}\n\n"
        f"🔥 Ликвидация: ${liq:,.4f} ({liq_dist}% от входа)"
    )
