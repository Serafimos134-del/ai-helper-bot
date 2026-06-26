"""
services/database.py
Refactored database layer with atomic transactions, thread safety, retry logic.
Prepared for multi-user support (user_id column added).
"""

import sqlite3
import threading
import time
import functools
import logging
import os

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
    All tables include user_id for future multi-user support."""

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
        """Create tables/indexes if missing, add new columns safely (including user_id)."""
        with self.lock:
            self.conn.executescript("""
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
                CREATE INDEX IF NOT EXISTS idx_closed_symbol ON closed_trades(symbol);
                CREATE INDEX IF NOT EXISTS idx_closed_date ON closed_trades(close_time);
                CREATE INDEX IF NOT EXISTS idx_closed_user_id ON closed_trades(user_id);
                CREATE INDEX IF NOT EXISTS idx_open_user_id ON open_trades(user_id);
            """)

            # Add missing columns (safe migration — adds user_id if missing)
            for table, cols in {
                'open_trades': [
                    ('user_id', "TEXT DEFAULT 'default'"),
                    ('orderId', 'TEXT NOT NULL DEFAULT ""'),
                    ('stop_loss', 'REAL'),
                    ('take_profit', 'REAL'),
                    ('entry_comment', "TEXT DEFAULT ''")
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
                    ('judge_verdict', 'TEXT')
                ]
            }.items():
                for col, col_def in cols:
                    try:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                        logger.info(f"Added column {col} to {table}")
                    except sqlite3.OperationalError:
                        pass

            # Update existing rows to have default user_id if they were NULL
            self.conn.execute("UPDATE open_trades SET user_id = 'default' WHERE user_id IS NULL")
            self.conn.execute("UPDATE closed_trades SET user_id = 'default' WHERE user_id IS NULL")
            self.conn.execute("UPDATE trader_memory SET user_id = 'default' WHERE user_id IS NULL")

            # Cleanup any NULL orderIds (shouldn't exist with NOT NULL, but just in case)
            self.conn.execute("DELETE FROM open_trades WHERE orderId IS NULL OR orderId = ''")
            # Ensure unique indexes
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_open_orderId ON open_trades(orderId)")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_closed_orderId ON closed_trades(orderId)")
            self.conn.commit()

    # ==================== thread-safe execution ====================
    @retry_on_locked()
    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute SQL with lock and retry. Returns cursor for further use."""
        with self.lock:
            return self.conn.execute(sql, params)

    def _commit(self):
        with self.lock:
            self.conn.commit()

    def _rollback(self):
        with self.lock:
            self.conn.rollback()

    def transaction(self):
        """Context manager for a transaction block."""
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

    # ==================== atomic trade closing ====================
    def close_trade_atomic(self, order_id: str, closed_trade_data: dict) -> int:
        """
        Atomically moves a trade from open_trades to closed_trades.
        Returns the new closed_trade id, or raises an exception on failure.
        """
        with self.transaction():
            # Check open trade exists
            cursor = self._execute("SELECT id FROM open_trades WHERE orderId = ?", (order_id,))
            open_row = cursor.fetchone()
            if not open_row:
                raise ValueError(f"Open trade with orderId {order_id} not found")

            # Insert into closed_trades
            insert_sql = """
                INSERT INTO closed_trades 
                (user_id, orderId, symbol, side, entry_price, exit_price, quantity, realized_pnl, comment,
                 risk_percent, leverage, stop_loss, take_profit, risk_reward,
                 open_time, close_time, entry_comment, exit_comment, ai_review,
                 holding_minutes, btc_price, eth_price, market_trend, setup_type, mistakes, ai_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                closed_trade_data.get('ai_score')
            )
            self._execute(insert_sql, params)
            new_id = self._execute("SELECT last_insert_rowid()").fetchone()[0]

            # Delete from open_trades
            self._execute("DELETE FROM open_trades WHERE orderId = ?", (order_id,))
            logger.info(f"Trade closed atomically: orderId={order_id}, new closed_id={new_id}")
            return new_id

    # ==================== existing methods (backward compatible) ====================
    def get_open_trades(self, user_id: str = 'default'):
        rows = self._execute("SELECT * FROM open_trades WHERE user_id = ?", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def add_open_trade(self, trade: dict):
        """Insert a new open trade. Raises IntegrityError if orderId already exists."""
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
                   'stop_loss', 'take_profit', 'entry_comment']
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
                   'stop_loss', 'take_profit']
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
        """Deprecated: kept for compatibility, does nothing since orderId is NOT NULL."""
        logger.debug("cleanup_orphan_open_trades skipped (no NULL orderIds allowed)")

    def add_closed_trade(self, trade: dict):
        """Insert a closed trade. Raises IntegrityError if orderId already exists."""
        if not trade.get('orderId'):
            raise ValueError("orderId is required for closed trade")
        sql = """
            INSERT INTO closed_trades 
            (user_id, orderId, symbol, side, entry_price, exit_price, quantity, realized_pnl, comment,
             risk_percent, leverage, stop_loss, take_profit, risk_reward,
             open_time, close_time, entry_comment, exit_comment, ai_review,
             holding_minutes, btc_price, eth_price, market_trend, setup_type, mistakes, ai_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            trade.get('ai_score')
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

    def add_comment(self, trade_id: int, comment: str):
        with self.transaction():
            cursor = self._execute(
                "UPDATE closed_trades SET exit_comment = ? WHERE id = ?",
                (comment, trade_id)
            )
            if cursor.rowcount == 0:
                logger.warning(f"add_comment: trade_id {trade_id} not found")

    def find_trade_by_id(self, trade_id: int):
        row = self._execute("SELECT * FROM closed_trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def update_trade_metrics(self, trade_id: int, **kwargs):
        allowed = ['risk_percent', 'leverage', 'stop_loss', 'take_profit', 'risk_reward',
                   'entry_comment', 'exit_comment', 'ai_review',
                   'holding_minutes', 'btc_price', 'eth_price', 'market_trend',
                   'setup_type', 'mistakes', 'ai_score',
                   'market_review', 'risk_review', 'psychology_review', 'judge_verdict']
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

def add_comment(trade_id, comment):
    _default_db.add_comment(trade_id, comment)

def find_trade_by_id(trade_id):
    return _default_db.find_trade_by_id(trade_id)

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