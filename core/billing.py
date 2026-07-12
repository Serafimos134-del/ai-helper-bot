"""
core/billing.py
Мультитенантность, Этап 4 (см. MULTITENANCY_MIGRATION_PLAN.md) — параметры
тарифа. Единственное место, где меняется цена/срок подписки и длительность
пробного периода.
"""

TRIAL_PERIOD_DAYS = 14
SUBSCRIPTION_PERIOD_DAYS = 14
SUBSCRIPTION_PRICE_USDT = 4
SUBSCRIPTION_ASSET = 'USDT'
