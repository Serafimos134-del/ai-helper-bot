"""
services/database.py
Refactored database layer with atomic transactions, thread safety, retry logic.
Multi-user support: users table + behavior_events for Behavior Alerts Engine.
Trade Management Engine v2: added idea, invalidation_sl, dca_count, tp_zones to open_trades.
"""

import sqlite3
import threading
import time
import functools
import logging
import os

from services.crypto_utils import encrypt, decrypt

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'trading.db')


# ======================== retry decorator ========================
def retry_on_locked(max_attempts: int = 3, delay: float = 0.2):
    """Retry on 'database is locked' errors."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempts < max_attempts:
                        attempts += 1
                        logger.warning(f"DB locked, retry {attempts}/{max_attempts}")
                        time.sleep(delay)
                    else:
                        raise
        return wrapper
    return decorator


class Database:
    """Thread-safe SQLite manager (singleton) with WAL mode, atomic close, retries.
    All tables include user_id for multi-user support."""

    _instance = None
    _lock_init = threading.Lock()

    def __new__(cls, db_path: str = DB_PATH):
        if cls._instance is None:
            with cls._lock_init:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: str = DB_PATH):
        if self._initialized:
            return
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._setup_pragmas()
        self._migrate()
        self._initialized = True

    def _setup_pragmas(self):
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA busy_timeout=5000;")
            self.conn.execute("PRAGMA foreign_keys=ON;")

    def _migrate(self):
        """Create tables/indexes if missing, add new columns safely (including idea, invalidation_sl, dca_count, tp_zones)."""
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    telegram_id TEXT UNIQUE NOT NULL,
                    username TEXT,
                    subscription_tier TEXT NOT NULL DEFAULT 'free',
                    subscription_expires_at TIMESTAMP,
                    bingx_api_key TEXT,
                    bingx_secret_key TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS open_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT DEFAULT 'default',
                    orderId TEXT UNIQUE NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
                    entry_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    leverage REAL DEFAULT 1,
                    unrealized_pnl REAL DEFAULT 0,
                    stop_loss REAL,
                    take_profit REAL,
                    entry_comment TEXT DEFAULT '',
                    idea TEXT,
                    invalidation_sl REAL,
                    dca_count INTEGER DEFAULT 0,
                    tp_zones TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS closed_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT DEFAULT 'default',
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    comment TEXT DEFAULT '',
                    risk_percent REAL DEFAULT 0,
                    leverage REAL DEFAULT 1,
                    stop_loss REAL,
                    take_profit REAL,
                    risk_reward REAL,
                    open_time TIMESTAMP,
                    close_time TIMESTAMP,
                    entry_comment TEXT DEFAULT '',
                    exit_comment TEXT DEFAULT '',
                    ai_review TEXT DEFAULT '',
                    holding_minutes INTEGER,
                    btc_price REAL,
                    eth_price REAL,
                    market_trend TEXT,
                    setup_type TEXT,
                    mistakes TEXT,
                    ai_score INTEGER,
                    closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS trader_memory (
                    user_id TEXT DEFAULT 'default',
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, category, key)
                );
                CREATE TABLE IF NOT EXISTS behavior_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT DEFAULT 'default',
                    event_type TEXT NOT NULL,
                    severity TEXT,
                    metadata TEXT,
                    order_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS trade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT DEFAULT 'default',
                    order_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS payments (
                    invoice_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    asset TEXT NOT NULL DEFAULT 'USDT',
                    days INTEGER NOT NULL DEFAULT 14,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    paid_at TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_closed_symbol ON closed_trades(symbol);
                CREATE INDEX IF NOT EXISTS idx_closed_date ON closed_trades(close_time);
                CREATE INDEX IF NOT EXISTS idx_closed_user_id ON closed_trades(user_id);
                CREATE INDEX IF NOT EXISTS idx_open_user_id ON open_trades(user_id);
                CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
                CREATE INDEX IF NOT EXISTS idx_behavior_user_id ON behavior_events(user_id);
                CREATE INDEX IF NOT EXISTS idx_trade_events_order_id ON trade_events(order_id);
                CREATE TABLE IF NOT EXISTS notification_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    notif_type TEXT NOT NULL,
                    sent_date TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, notif_type, sent_date)
                );
                CREATE TABLE IF NOT EXISTS user_risk_profile (
                    user_id TEXT PRIMARY KEY,
                    risk_level TEXT,
                    trading_style TEXT,
                    experience_level TEXT,
                    risk_goal TEXT,
                    risk_score INTEGER,
                    risk_score_components TEXT,
                    onboarding_completed INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
                CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
            """)

            # Add missing columns (safe migration)
            for table, cols in {
                'open_trades': [
                    ('user_id', "TEXT DEFAULT 'default'"),
                    ('orderId', 'TEXT NOT NULL DEFAULT ""'),
                    ('stop_loss', 'REAL'),
                    ('take_profit', 'REAL'),
                    ('entry_comment', "TEXT DEFAULT ''"),
                    ('idea', 'TEXT'),
                    ('invalidation_sl', 'REAL'),
                    ('dca_count', 'INTEGER DEFAULT 0'),
                    ('tp_zones', 'TEXT'),
                ],
                'closed_trades': [
                    ('user_id', "TEXT DEFAULT 'default'"),
                    ('orderId', 'TEXT'),
                    ('risk_percent', 'REAL DEFAULT 0'),
                    ('leverage', 'REAL DEFAULT 1'),
                    ('stop_loss', 'REAL'),
                    ('take_profit', 'REAL'),
                    ('risk_reward', 'REAL'),
                    ('open_time', 'TIMESTAMP'),
                    ('close_time', 'TIMESTAMP'),
                    ('entry_comment', "TEXT DEFAULT ''"),
                    ('exit_comment', "TEXT DEFAULT ''"),
                    ('ai_review', "TEXT DEFAULT ''"),
                    ('holding_minutes', 'INTEGER'),
                    ('btc_price', 'REAL'),
                    ('eth_price', 'REAL'),
                    ('market_trend', 'TEXT'),
                    ('setup_type', 'TEXT'),
                    ('mistakes', 'TEXT'),
                    ('ai_score', 'INTEGER'),
                    ('market_review', 'TEXT'),
                    ('risk_review', 'TEXT'),
                    ('psychology_review', 'TEXT'),
                    ('judge_verdict', 'TEXT'),
                    ('score_breakdown', 'TEXT'),
                    ('dca_count', 'INTEGER DEFAULT 0'),
                ],
                'users': [
                    ('subscription_tier', "TEXT NOT NULL DEFAULT 'free'"),
                    ('subscription_expires_at', 'TIMESTAMP'),
                    ('bingx_api_key', 'TEXT'),
                    ('bingx_secret_key', 'TEXT'),
                    ('last_active_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
                    ('notifications_enabled', 'INTEGER NOT NULL DEFAULT 1'),
                ],
                'behavior_events': [
                    ('order_id', 'TEXT'),
                ],
                'payments': [
                    ('days', 'INTEGER NOT NULL DEFAULT 14'),
                ],
            }.items():
                for col, col_def in cols:
                    try:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                        logger.info(f"Added column {col} to {table}")
                    except sqlite3.OperationalError:
                        pass

            for tbl in ['open_trades', 'closed_trades', 'trader_memory']:
                try:
                    self.conn.execute(f"UPDATE {tbl} SET user_id = 'default' WHERE user_id IS NULL")
                except sqlite3.OperationalError:
                    pass

            self.conn.execute("DELETE FROM open_trades WHERE orderId IS NULL OR orderId = ''")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_open_orderId ON open_trades(orderId)")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_closed_orderId ON closed_trades(orderId)")
            # order_id добавлен в behavior_events через ALTER TABLE выше (для
            # существующих БД) — индекс создаётся здесь, после того как
            # колонка гарантированно есть, а не в executescript() наверху
            # (там CREATE TABLE IF NOT EXISTS — no-op для старых БД, и
            # CREATE INDEX на ещё не добавленную колонку падает с
            # "no such column").
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_order_id ON behavior_events(order_id)")

            default_telegram_id = os.getenv('TELEGRAM_CHAT_ID', 'default')
            self.conn.execute(
                "INSERT OR IGNORE INTO users (user_id, telegram_id, subscription_tier) VALUES (?, ?, 'premium')",
                ('default', default_telegram_id)
            )
            self.conn.commit()

    # ==================== thread-safe execution ====================
    @retry_on_locked()
    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def _commit(self):
        with self.lock:
            self.conn.commit()

    def _rollback(self):
        with self.lock:
            self.conn.rollback()

    def transaction(self):
        class TransactionContext:
            def __init__(self, db):
                self.db = db
            def __enter__(self):
                return self.db
            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is None:
                    self.db._commit()
                else:
                    self.db._rollback()
                return False
        return TransactionContext(self)

    # ==================== users / multi-user ====================
    def get_or_create_user(self, telegram_id: str, username: str = None) -> dict:
        telegram_id = str(telegram_id)
        row = self._execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row:
            with self.transaction():
                self._execute(
                    "UPDATE users SET last_active_at = CURRENT_TIMESTAMP WHERE telegram_id = ?",
                    (telegram_id,)
                )
            return dict(row)

        # Новый пользователь получает бесплатный триал (core/billing.py,
        # TRIAL_PERIOD_DAYS) сразу при регистрации — Этап 4 плана миграции:
        # "Сначала бесплатные 14 дней потом подписка". Тариф 'premium' с
        # ограниченным сроком, а не 'free' — is_premium() ничего не знает
        # про триалы отдельно, expiry делает всё сам.
        from datetime import datetime, timedelta
        from core.billing import TRIAL_PERIOD_DAYS
        trial_expires_at = (datetime.now() + timedelta(days=TRIAL_PERIOD_DAYS)).isoformat()
        with self.transaction():
            self._execute(
                "INSERT INTO users (user_id, telegram_id, username, subscription_tier, subscription_expires_at) "
                "VALUES (?, ?, ?, 'premium', ?)",
                (telegram_id, telegram_id, username, trial_expires_at)
            )
        logger.info(f"Новый пользователь зарегистрирован: telegram_id={telegram_id}, триал до {trial_expires_at}")
        row = self._execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return dict(row)

    def get_user(self, user_id: str) -> dict:
        row = self._execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def is_premium(self, user_id: str) -> bool:
        user = self.get_user(user_id)
        if not user:
            return False
        if user.get('subscription_tier') != 'premium':
            return False
        expires = user.get('subscription_expires_at')
        if expires is None:
            return True
        from datetime import datetime
        try:
            return datetime.fromisoformat(expires) > datetime.now()
        except Exception:
            return True

    def set_subscription(self, user_id: str, tier: str, expires_at: str = None):
        with self.transaction():
            self._execute(
                "UPDATE users SET subscription_tier = ?, subscription_expires_at = ? WHERE user_id = ?",
                (tier, expires_at, user_id)
            )

    def extend_subscription(self, user_id: str, days: int) -> str:
        """Продлевает подписку на `days` дней от текущей даты истечения,
        если она ещё в будущем (не сгорает остаток при досрочной оплате),
        иначе от текущего момента (истёкшая/впервые оплаченная подписка).
        Возвращает новую дату истечения (ISO)."""
        from datetime import datetime, timedelta
        now = datetime.now()
        user = self.get_user(user_id)
        current_expiry = None
        if user and user.get('subscription_expires_at'):
            try:
                current_expiry = datetime.fromisoformat(user['subscription_expires_at'])
            except (ValueError, TypeError):
                current_expiry = None
        base = current_expiry if (current_expiry and current_expiry > now) else now
        new_expiry = (base + timedelta(days=days)).isoformat()
        self.set_subscription(user_id, 'premium', new_expiry)
        return new_expiry

    # ==================== payments (Crypto Pay) ====================
    def create_payment(self, invoice_id, user_id: str, amount: float, asset: str = 'USDT', days: int = 14):
        with self.transaction():
            self._execute(
                "INSERT INTO payments (invoice_id, user_id, amount, asset, days, status) VALUES (?, ?, ?, ?, ?, 'active')",
                (str(invoice_id), user_id, amount, asset, days)
            )

    def get_pending_payments(self) -> list:
        rows = self._execute("SELECT * FROM payments WHERE status = 'active'").fetchall()
        return [dict(row) for row in rows]

    def mark_payment_paid(self, invoice_id) -> dict:
        """Идемпотентно помечает счёт оплаченным. Возвращает запись платежа,
        если это первое начисление, либо None если счёт уже был обработан
        раньше — защита от двойного продления подписки при повторном опросе
        Crypto Pay (см. core/scheduler.py:crypto_pay_poll_job)."""
        row = self._execute(
            "SELECT * FROM payments WHERE invoice_id = ?", (str(invoice_id),)
        ).fetchone()
        if not row or row['status'] == 'paid':
            return None
        with self.transaction():
            self._execute(
                "UPDATE payments SET status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE invoice_id = ?",
                (str(invoice_id),)
            )
        return dict(row)

    def mark_payment_expired(self, invoice_id) -> None:
        """Помечает неоплаченный счёт истёкшим — без этого брошенные
        чек-ауты (Crypto Pay сам истекает инвойс через expires_in=3600,
        см. services/crypto_pay.py) навсегда остаются status='active' и
        get_pending_payments() опрашивает их у Crypto Pay бесконечно,
        неограниченно накапливаясь с каждым непройденным до оплаты
        /subscribe (см. core/scheduler.py:crypto_pay_poll_job)."""
        with self.transaction():
            self._execute(
                "UPDATE payments SET status = 'expired' WHERE invoice_id = ? AND status = 'active'",
                (str(invoice_id),)
            )

    def set_bingx_keys(self, user_id: str, api_key: str, secret_key: str):
        with self.transaction():
            self._execute(
                "UPDATE users SET bingx_api_key = ?, bingx_secret_key = ? WHERE user_id = ?",
                (encrypt(api_key), encrypt(secret_key), user_id)
            )

    def get_bingx_keys(self, user_id: str) -> tuple:
        user = self.get_user(user_id)
        if not user:
            return None, None
        return decrypt(user.get('bingx_api_key')), decrypt(user.get('bingx_secret_key'))

    def get_all_active_users(self) -> list:
        rows = self._execute(
            "SELECT * FROM users WHERE bingx_api_key IS NOT NULL AND bingx_api_key != ''"
        ).fetchall()
        users = [dict(row) for row in rows]
        for u in users:
            u['bingx_api_key'] = decrypt(u.get('bingx_api_key'))
            u['bingx_secret_key'] = decrypt(u.get('bingx_secret_key'))
        return users

    def get_users_for_background_jobs(self) -> list:
        """Подписчики (триал или оплата) с привязанными собственными
        BingX-ключами — только для них возможны пер-пользовательская
        синхронизация/сопровождение/отчёты (core/scheduler.py). Владелец
        (глобальные ключи из .env, без записи в users.bingx_api_key) сюда
        не попадает — обслуживается отдельными owner-джобами с
        фиксированным chat_id, как раньше. Иначе пользователь без своих
        ключей получил бы в отчёте баланс владельца (contextvar-фолбэк
        на .env в services/bingx_api.py) — утечка чужих данных."""
        return [u for u in self.get_all_active_users() if self.is_premium(u['user_id'])]

    def set_notifications_enabled(self, user_id: str, enabled: bool):
        with self.transaction():
            self._execute(
                "UPDATE users SET notifications_enabled = ? WHERE user_id = ?",
                (1 if enabled else 0, user_id)
            )

    def try_log_notification(self, user_id: str, notif_type: str, sent_date: str) -> bool:
        """Атомарно резервирует отправку (user_id, notif_type, sent_date).
        True — можно отправлять (первый раз за эту дату), False — уже
        отправляли (защита от дублей при рестарте/повторном тике job_queue)."""
        try:
            with self.transaction():
                self._execute(
                    "INSERT INTO notification_log (user_id, notif_type, sent_date) VALUES (?, ?, ?)",
                    (user_id, notif_type, sent_date)
                )
            return True
        except sqlite3.IntegrityError:
            return False

    # ==================== risk profile ====================
    def _ensure_risk_profile_row(self, user_id: str):
        with self.transaction():
            self._execute(
                "INSERT OR IGNORE INTO user_risk_profile (user_id) VALUES (?)",
                (user_id,)
            )

    def get_risk_profile(self, user_id: str) -> dict:
        row = self._execute(
            "SELECT * FROM user_risk_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_risk_profile(self, user_id: str, risk_level: str = None, trading_style: str = None,
                          experience_level: str = None, risk_goal: str = None):
        """Заявленный пользователем профиль (п.1 ТЗ) — не путать с
        фактическим Risk Score (см. save_risk_score), тот считается
        отдельно из реальных сделок (ai/risk_profile.py)."""
        self._ensure_risk_profile_row(user_id)
        fields, params = [], []
        for col, val in (
            ('risk_level', risk_level), ('trading_style', trading_style),
            ('experience_level', experience_level), ('risk_goal', risk_goal),
        ):
            if val is not None:
                fields.append(f"{col} = ?")
                params.append(val)
        if not fields:
            return
        params.append(user_id)
        with self.transaction():
            self._execute(
                f"UPDATE user_risk_profile SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                tuple(params)
            )

    def complete_risk_onboarding(self, user_id: str):
        self._ensure_risk_profile_row(user_id)
        with self.transaction():
            self._execute(
                "UPDATE user_risk_profile SET onboarding_completed = 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (user_id,)
            )

    def save_risk_score(self, user_id: str, risk_score: int, components: dict):
        """Персистит последний посчитанный Risk Score (ai/risk_profile.py:
        compute_risk_score) — AI Core (ai/context_builder.py) читает этот
        снимок синхронно из БД, не пересчитывает заново на каждый вызов
        консилиума (это требовало бы лишнего live-запроса баланса на
        каждый /consilium)."""
        import json as _json
        self._ensure_risk_profile_row(user_id)
        with self.transaction():
            self._execute(
                "UPDATE user_risk_profile SET risk_score = ?, risk_score_components = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (risk_score, _json.dumps(components, ensure_ascii=False), user_id)
            )

    # ==================== behavior alerts engine ====================
    def add_behavior_event(self, user_id: str, event_type: str, severity: str, metadata: str, order_id: str = None):
        with self.transaction():
            self._execute(
                "INSERT INTO behavior_events (user_id, event_type, severity, metadata, order_id) VALUES (?, ?, ?, ?, ?)",
                (user_id, event_type, severity, metadata, order_id)
            )

    def get_recent_behavior_events(self, user_id: str, event_type: str = None, limit: int = 20):
        if event_type:
            rows = self._execute(
                "SELECT * FROM behavior_events WHERE user_id = ? AND event_type = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, event_type, limit)
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM behavior_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(row) for row in rows]

    # ==================== trader memory (Этап 8 плана AI Trading Core) ====
    # Хронологический журнал по каждой сделке (order_id): анализ открытия,
    # события сопровождения (перенос стопа/безубыток/частичная фиксация/DCA/
    # закрытие), анализ закрытия. Основа для будущего Trader DNA (Этап 9) —
    # там понадобится не только итоговый снимок сделки (он уже есть в
    # closed_trades), а последовательность решений внутри её жизни.
    def add_trade_event(self, order_id: str, event_type: str, payload: str, user_id: str = 'default'):
        with self.transaction():
            self._execute(
                "INSERT INTO trade_events (user_id, order_id, event_type, payload) VALUES (?, ?, ?, ?)",
                (user_id, order_id, event_type, payload)
            )

    def get_trade_events(self, order_id: str, user_id: str = 'default'):
        rows = self._execute(
            "SELECT * FROM trade_events WHERE user_id = ? AND order_id = ? ORDER BY created_at ASC",
            (user_id, order_id)
        ).fetchall()
        return [dict(row) for row in rows]

    # ==================== atomic trade closing ====================
    def close_trade_atomic(self, order_id: str, closed_trade_data: dict) -> int:
        with self.transaction():
            cursor = self._execute("SELECT id FROM open_trades WHERE orderId = ?", (order_id,))
            open_row = cursor.fetchone()
            if not open_row:
                raise ValueError(f"Open trade with orderId {order_id} not found")

            insert_sql = """
                INSERT INTO closed_trades
                (user_id, orderId, symbol, side, entry_price, exit_price, quantity, realized_pnl, comment,
                 risk_percent, leverage, stop_loss, take_profit, risk_reward,
                 open_time, close_time, entry_comment, exit_comment, ai_review,
                 holding_minutes, btc_price, eth_price, market_trend, setup_type, mistakes, ai_score,
                 dca_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                closed_trade_data.get('user_id', 'default'),
                closed_trade_data.get('orderId', order_id),
                closed_trade_data['symbol'],
                closed_trade_data['side'],
                closed_trade_data['entry_price'],
                closed_trade_data['exit_price'],
                closed_trade_data['quantity'],
                closed_trade_data['realized_pnl'],
                closed_trade_data.get('comment', ''),
                closed_trade_data.get('risk_percent', 0),
                closed_trade_data.get('leverage', 1),
                closed_trade_data.get('stop_loss'),
                closed_trade_data.get('take_profit'),
                closed_trade_data.get('risk_reward'),
                closed_trade_data.get('open_time'),
                closed_trade_data.get('close_time'),
                closed_trade_data.get('entry_comment', ''),
                closed_trade_data.get('exit_comment', ''),
                closed_trade_data.get('ai_review', ''),
                closed_trade_data.get('holding_minutes'),
                closed_trade_data.get('btc_price'),
                closed_trade_data.get('eth_price'),
                closed_trade_data.get('market_trend'),
                closed_trade_data.get('setup_type'),
                closed_trade_data.get('mistakes'),
                closed_trade_data.get('ai_score'),
                closed_trade_data.get('dca_count', 0)
            )
            self._execute(insert_sql, params)
            new_id = self._execute("SELECT last_insert_rowid()").fetchone()[0]
            self._execute("DELETE FROM open_trades WHERE orderId = ?", (order_id,))
            logger.info(f"Trade closed atomically: orderId={order_id}, new closed_id={new_id}")
            return new_id

    # ==================== trades (backward compatible) ====================
    def get_open_trades(self, user_id: str = 'default'):
        rows = self._execute("SELECT * FROM open_trades WHERE user_id = ?", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def add_open_trade(self, trade: dict):
        if not trade.get('orderId'):
            raise ValueError("orderId is required")
        sql = """
            INSERT INTO open_trades (user_id, orderId, symbol, side, entry_price, quantity, leverage,
                                     unrealized_pnl, stop_loss, take_profit, entry_comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self.transaction():
            self._execute(sql, (
                trade.get('user_id', 'default'),
                trade['orderId'],
                trade['symbol'], trade['side'], trade['entry_price'],
                trade['quantity'], trade.get('leverage', 1),
                trade.get('unrealized_pnl', 0),
                trade.get('stop_loss'), trade.get('take_profit'),
                trade.get('entry_comment', '')
            ))
            logger.info(f"Open trade added: orderId={trade['orderId']}")

    def update_open_trade(self, symbol: str, **kwargs):
        allowed = ['unrealized_pnl', 'leverage', 'quantity', 'entry_price',
                   'stop_loss', 'take_profit', 'entry_comment',
                   'idea', 'invalidation_sl', 'dca_count', 'tp_zones']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [symbol]
        with self.transaction():
            cursor = self._execute(f"UPDATE open_trades SET {set_clause} WHERE symbol=?", values)
            if cursor.rowcount == 0:
                logger.warning(f"update_open_trade: no rows updated for symbol={symbol}")

    def update_open_trade_by_order_id(self, order_id: str, **kwargs):
        allowed = ['entry_comment', 'unrealized_pnl', 'leverage', 'quantity', 'entry_price',
                   'stop_loss', 'take_profit',
                   'idea', 'invalidation_sl', 'dca_count', 'tp_zones']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [order_id]
        with self.transaction():
            cursor = self._execute(f"UPDATE open_trades SET {set_clause} WHERE orderId=?", values)
            if cursor.rowcount == 0:
                logger.warning(f"update_open_trade_by_order_id: orderId {order_id} not found")

    def delete_open_trade(self, symbol: str):
        with self.transaction():
            cursor = self._execute("DELETE FROM open_trades WHERE symbol=?", (symbol,))
            if cursor.rowcount == 0:
                logger.warning(f"delete_open_trade: no rows for symbol={symbol}")

    def delete_open_trade_by_order_id(self, order_id: str):
        with self.transaction():
            cursor = self._execute("DELETE FROM open_trades WHERE orderId=?", (order_id,))
            if cursor.rowcount == 0:
                logger.warning(f"delete_open_trade_by_order_id: orderId {order_id} not found")

    def cleanup_orphan_open_trades(self):
        logger.debug("cleanup_orphan_open_trades skipped (no NULL orderIds allowed)")

    def add_closed_trade(self, trade: dict):
        if not trade.get('orderId'):
            raise ValueError("orderId is required for closed trade")
        sql = """
            INSERT INTO closed_trades
            (user_id, orderId, symbol, side, entry_price, exit_price, quantity, realized_pnl, comment,
             risk_percent, leverage, stop_loss, take_profit, risk_reward,
             open_time, close_time, entry_comment, exit_comment, ai_review,
             holding_minutes, btc_price, eth_price, market_trend, setup_type, mistakes, ai_score,
             dca_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            trade.get('user_id', 'default'),
            trade['orderId'],
            trade['symbol'], trade['side'], trade['entry_price'], trade['exit_price'],
            trade['quantity'], trade['realized_pnl'], trade.get('comment', ''),
            trade.get('risk_percent', 0), trade.get('leverage', 1),
            trade.get('stop_loss'), trade.get('take_profit'), trade.get('risk_reward'),
            trade.get('open_time'), trade.get('close_time'),
            trade.get('entry_comment', ''), trade.get('exit_comment', ''),
            trade.get('ai_review', ''),
            trade.get('holding_minutes'), trade.get('btc_price'), trade.get('eth_price'),
            trade.get('market_trend'), trade.get('setup_type'), trade.get('mistakes'),
            trade.get('ai_score'),
            trade.get('dca_count', 0)
        )
        with self.transaction():
            self._execute(sql, params)
            logger.info(f"Closed trade added: orderId={trade['orderId']}")

    def get_closed_trades(self, limit: int = 50, symbol: str = None, user_id: str = 'default'):
        query = "SELECT * FROM closed_trades WHERE (entry_price != 0 OR realized_pnl != 0) AND user_id = ?"
        params = [user_id]
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY close_time DESC LIMIT ?"
        params.append(limit)
        rows = self._execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self, user_id: str = 'default'):
        row = self._execute("""
            SELECT
                COUNT(*) AS total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS winning_trades,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losing_trades,
                SUM(realized_pnl) AS total_pnl,
                AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) AS avg_profit,
                AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END) AS avg_loss,
                MAX(realized_pnl) AS best_trade,
                MIN(realized_pnl) AS worst_trade
            FROM closed_trades
            WHERE user_id = ? AND realized_pnl != 0 AND (entry_price != 0 OR realized_pnl != 0)
        """, (user_id,)).fetchone()

        total = row['total_trades'] or 0
        if total == 0:
            return {
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'win_rate': 0, 'total_pnl': 0.0, 'avg_profit': 0.0, 'avg_loss': 0.0,
                'best_trade': 0.0, 'best_trade_symbol': '',
                'worst_trade': 0.0, 'worst_trade_symbol': '',
                'unrealized_pnl': 0, 'open_positions': 0
            }

        best_row = self._execute(
            "SELECT symbol FROM closed_trades WHERE user_id = ? AND realized_pnl = ? AND (entry_price != 0 OR realized_pnl != 0) ORDER BY close_time DESC LIMIT 1",
            (user_id, row['best_trade'])
        ).fetchone()
        worst_row = self._execute(
            "SELECT symbol FROM closed_trades WHERE user_id = ? AND realized_pnl = ? AND (entry_price != 0 OR realized_pnl != 0) ORDER BY close_time DESC LIMIT 1",
            (user_id, row['worst_trade'])
        ).fetchone()

        unrealized = self._execute("SELECT SUM(unrealized_pnl) FROM open_trades WHERE user_id = ?", (user_id,)).fetchone()[0] or 0.0
        open_count = self._execute("SELECT COUNT(*) FROM open_trades WHERE user_id = ?", (user_id,)).fetchone()[0]

        return {
            'total_trades': total,
            'winning_trades': row['winning_trades'],
            'losing_trades': row['losing_trades'],
            'win_rate': (row['winning_trades'] / total) * 100 if total > 0 else 0,
            'total_pnl': row['total_pnl'] or 0.0,
            'avg_profit': row['avg_profit'] or 0.0,
            'avg_loss': row['avg_loss'] or 0.0,
            'best_trade': row['best_trade'] or 0.0,
            'best_trade_symbol': best_row['symbol'] if best_row else '',
            'worst_trade': row['worst_trade'] or 0.0,
            'worst_trade_symbol': worst_row['symbol'] if worst_row else '',
            'unrealized_pnl': unrealized,
            'open_positions': open_count
        }

    def add_comment(self, trade_id: int, comment: str, user_id: str = None):
        # user_id=None сохраняет старое поведение (без изоляции) для
        # внутренних/legacy вызовов; хендлеры, обрабатывающие
        # пользовательский callback_data (trade_id приходит от клиента,
        # потенциально можно подставить чужой id), обязаны передавать
        # user_id текущего пользователя — иначе один подписчик сможет
        # прочитать/изменить чужую сделку, просто подобрав числовой id
        # (см. MULTITENANCY_MIGRATION_PLAN.md, "разграничение данных").
        if user_id is not None:
            sql = "UPDATE closed_trades SET exit_comment = ? WHERE id = ? AND user_id = ?"
            params = (comment, trade_id, user_id)
        else:
            sql = "UPDATE closed_trades SET exit_comment = ? WHERE id = ?"
            params = (comment, trade_id)
        with self.transaction():
            cursor = self._execute(sql, params)
            if cursor.rowcount == 0:
                logger.warning(f"add_comment: trade_id {trade_id} not found (user_id={user_id})")

    def find_trade_by_id(self, trade_id: int, user_id: str = None):
        if user_id is not None:
            row = self._execute(
                "SELECT * FROM closed_trades WHERE id = ? AND user_id = ?", (trade_id, user_id)
            ).fetchone()
        else:
            row = self._execute("SELECT * FROM closed_trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def update_trade_metrics(self, trade_id: int, **kwargs):
        allowed = ['risk_percent', 'leverage', 'stop_loss', 'take_profit', 'risk_reward',
                   'entry_comment', 'exit_comment', 'ai_review',
                   'holding_minutes', 'btc_price', 'eth_price', 'market_trend',
                   'setup_type', 'mistakes', 'ai_score',
                   'market_review', 'risk_review', 'psychology_review', 'judge_verdict',
                   'score_breakdown']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [trade_id]
        with self.transaction():
            cursor = self._execute(f"UPDATE closed_trades SET {set_clause} WHERE id=?", values)
            if cursor.rowcount == 0:
                logger.warning(f"update_trade_metrics: trade_id {trade_id} not found")

    def get_last_closed_id(self):
        row = self._execute("SELECT MAX(id) FROM closed_trades").fetchone()
        return row[0] if row else None

    # ─── Memory Engine (user-scoped) ───
    def memory_set(self, category: str, key: str, value: str, user_id: str = 'default'):
        sql = """
            INSERT INTO trader_memory (user_id, category, key, value, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, category, key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """
        with self.transaction():
            self._execute(sql, (user_id, category, key, value))

    def memory_get(self, category: str, key: str, user_id: str = 'default'):
        row = self._execute(
            "SELECT value FROM trader_memory WHERE user_id = ? AND category = ? AND key = ?",
            (user_id, category, key)
        ).fetchone()
        return row['value'] if row else None

    def memory_get_all(self, category: str, user_id: str = 'default'):
        rows = self._execute(
            "SELECT key, value FROM trader_memory WHERE user_id = ? AND category = ?",
            (user_id, category)
        ).fetchall()
        return {row['key']: row['value'] for row in rows}


# ─── backward compatible module-level proxies ───
_default_db = Database()

def get_open_trades(user_id='default'):
    return _default_db.get_open_trades(user_id)

def add_open_trade(trade):
    _default_db.add_open_trade(trade)

def update_open_trade(symbol, **kwargs):
    _default_db.update_open_trade(symbol, **kwargs)

def update_open_trade_by_order_id(order_id, **kwargs):
    _default_db.update_open_trade_by_order_id(order_id, **kwargs)

def delete_open_trade(symbol):
    _default_db.delete_open_trade(symbol)

def delete_open_trade_by_order_id(order_id):
    _default_db.delete_open_trade_by_order_id(order_id)

def cleanup_orphan_open_trades():
    _default_db.cleanup_orphan_open_trades()

def add_closed_trade(trade):
    _default_db.add_closed_trade(trade)

def get_closed_trades(limit=50, symbol=None, user_id='default'):
    return _default_db.get_closed_trades(limit, symbol, user_id)

def get_stats(user_id='default'):
    return _default_db.get_stats(user_id)

def add_comment(trade_id, comment, user_id=None):
    _default_db.add_comment(trade_id, comment, user_id)

def find_trade_by_id(trade_id, user_id=None):
    return _default_db.find_trade_by_id(trade_id, user_id)

def update_trade_metrics(trade_id, **kwargs):
    _default_db.update_trade_metrics(trade_id, **kwargs)

def get_last_closed_id():
    return _default_db.get_last_closed_id()

def memory_set(category, key, value, user_id='default'):
    _default_db.memory_set(category, key, value, user_id)

def memory_get(category, key, user_id='default'):
    return _default_db.memory_get(category, key, user_id)

def memory_get_all(category, user_id='default'):
    return _default_db.memory_get_all(category, user_id)

def close_trade_atomic(order_id, closed_trade_data):
    return _default_db.close_trade_atomic(order_id, closed_trade_data)

def init_db():
    pass