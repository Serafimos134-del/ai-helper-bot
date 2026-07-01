"""
services/calc_engine.py
Position calculator — маржа, ликвидация, риск.
"""


def calculate_position(symbol: str, price: float, leverage: int,
                        balance: float, risk_percent: float = 1.0) -> dict:
    """
    Рассчитывает параметры позиции.

    Args:
        symbol: тикер (BTC-USDT)
        price: цена входа
        leverage: плечо (1-125)
        balance: баланс депозита в USDT
        risk_percent: риск от депо в % (по умолчанию 1%)

    Returns:
        dict с расчётами
    """
    if price <= 0 or leverage <= 0 or balance <= 0:
        return {'error': 'Некорректные данные'}

    # Размер позиции при риске risk_percent% от депо
    risk_amount   = balance * risk_percent / 100
    margin        = risk_amount * leverage
    position_size = margin * leverage / price  # в монетах
    notional      = position_size * price      # в USDT

    # Ликвидация (упрощённая формула для изолированной маржи)
    liq_long  = price * (1 - 1 / leverage + 0.005)  # +0.5% maintenance margin
    liq_short = price * (1 + 1 / leverage - 0.005)

    # Расстояние до ликвидации в %
    liq_distance_long  = round((price - liq_long)  / price * 100, 2)
    liq_distance_short = round((liq_short - price) / price * 100, 2)

    return {
        'symbol':         symbol.upper(),
        'price':          price,
        'leverage':       leverage,
        'balance':        balance,
        'risk_percent':   risk_percent,
        'risk_amount':    round(risk_amount, 2),
        'margin':         round(margin, 2),
        'position_size':  round(position_size, 4),
        'notional':       round(notional, 2),
        'liq_long':       round(liq_long, 4),
        'liq_short':      round(liq_short, 4),
        'liq_distance_long':  liq_distance_long,
        'liq_distance_short': liq_distance_short,
    }


def format_calc_result(result: dict, side: str = None) -> str:
    """Форматирует результат расчёта для Telegram."""
    if result.get('error'):
        return f"❌ {result['error']}"

    side_str = f" {side.upper()}" if side else ""
    liq = result['liq_long'] if (not side or side.upper() == 'LONG') else result['liq_short']
    liq_dist = result['liq_distance_long'] if (not side or side.upper() == 'LONG') else result['liq_distance_short']

    return (
        f"🧮 Калькулятор позиции\n\n"
        f"📌 {result['symbol']}{side_str} × {result['leverage']}x\n"
        f"💵 Цена входа: ${result['price']:,.4f}\n\n"
        f"💰 Баланс депо: ${result['balance']:,.2f}\n"
        f"⚠️ Риск: {result['risk_percent']}% = ${result['risk_amount']:,.2f}\n\n"
        f"📦 Маржа: ${result['margin']:,.2f}\n"
        f"📊 Размер позиции: {result['position_size']} {result['symbol'].split('-')[0]}\n"
        f"💼 Объём: ${result['notional']:,.2f}\n\n"
        f"🔥 Ликвидация: ${liq:,.4f} ({liq_dist}% от входа)"
    )
