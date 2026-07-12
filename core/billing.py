"""
core/billing.py
Мультитенантность, Этап 4 (см. MULTITENANCY_MIGRATION_PLAN.md) — параметры
тарифа. Единственное место, где меняются цены/сроки подписки и триала.
"""

TRIAL_PERIOD_DAYS = 14
SUBSCRIPTION_ASSET = 'USDT'

# plan_id используется как callback_data (см. handlers/subscription.py) —
# держим коротким и без пробелов/спецсимволов.
SUBSCRIPTION_PLANS = {
    '14d':  {'days': 14,  'price': 4,  'label': '14 дней'},
    '30d':  {'days': 30,  'price': 8,  'label': '1 месяц'},
    '180d': {'days': 180, 'price': 35, 'label': '6 месяцев'},
}
DEFAULT_PLAN = '14d'

# Обратная совместимость: код, написанный под Этап 4 до появления тарифной
# сетки (poll job уведомление и т.п.), может ссылаться на "базовый" тариф.
SUBSCRIPTION_PERIOD_DAYS = SUBSCRIPTION_PLANS[DEFAULT_PLAN]['days']
SUBSCRIPTION_PRICE_USDT = SUBSCRIPTION_PLANS[DEFAULT_PLAN]['price']
