import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'trading.db')

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS open_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orderId TEXT,
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
        CREATE INDEX IF NOT EXISTS idx_closed_symbol ON closed_trades(symbol);
        CREATE INDEX IF NOT EXISTS idx_closed_date ON closed_trades(close_time);
    """)
    for table, cols in {
        'open_trades': [
            ('orderId', 'TEXT'),
            ('stop_loss', 'REAL'),
            ('take_profit', 'REAL'),
            ('entry_comment', "TEXT DEFAULT ''")
        ],
        'closed_trades': [
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
            ('ai_score', 'INTEGER')
        ]
    }.items():
        for col, col_def in cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass
    conn.commit()
    conn.close()

init_db()

class Database:
    @staticmethod
    def get_open_trades():
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM open_trades").fetchall()
        conn.close()
        return [dict(row) for row in rows]

    @staticmethod
    def add_open_trade(trade: dict):
        conn = _get_conn()
        conn.execute("""
            INSERT INTO open_trades (orderId, symbol, side, entry_price, quantity, leverage,
                                    unrealized_pnl, stop_loss, take_profit, entry_comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get('orderId'),
            trade['symbol'], trade['side'], trade['entry_price'],
            trade['quantity'], trade.get('leverage', 1),
            trade.get('unrealized_pnl', 0),
            trade.get('stop_loss'), trade.get('take_profit'),
            trade.get('entry_comment', '')
        ))
        conn.commit()
        conn.close()

    @staticmethod
    def update_open_trade(symbol: str, **kwargs):
        allowed = ['unrealized_pnl', 'leverage', 'quantity', 'entry_price',
                   'stop_loss', 'take_profit', 'entry_comment']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [symbol]
        conn = _get_conn()
        conn.execute(f"UPDATE open_trades SET {set_clause} WHERE symbol=?", values)
        conn.commit()
        conn.close()

    @staticmethod
    def update_open_trade_by_order_id(order_id: str, **kwargs):
        allowed = ['entry_comment', 'unrealized_pnl', 'leverage', 'quantity', 'entry_price',
                   'stop_loss', 'take_profit']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [order_id]
        conn = _get_conn()
        conn.execute(f"UPDATE open_trades SET {set_clause} WHERE orderId=?", values)
        conn.commit()
        conn.close()

    @staticmethod
    def delete_open_trade(symbol: str):
        conn = _get_conn()
        conn.execute("DELETE FROM open_trades WHERE symbol=?", (symbol,))
        conn.commit()
        conn.close()

    @staticmethod
    def delete_open_trade_by_order_id(order_id: str):
        conn = _get_conn()
        conn.execute("DELETE FROM open_trades WHERE orderId=?", (order_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def cleanup_orphan_open_trades():
        conn = _get_conn()
        conn.execute("DELETE FROM open_trades WHERE orderId IS NULL")
        conn.commit()
        conn.close()

    @staticmethod
    def add_closed_trade(trade: dict):
        conn = _get_conn()
        conn.execute("""
            INSERT INTO closed_trades 
            (symbol, side, entry_price, exit_price, quantity, realized_pnl, comment,
             risk_percent, leverage, stop_loss, take_profit, risk_reward,
             open_time, close_time, entry_comment, exit_comment, ai_review,
             holding_minutes, btc_price, eth_price, market_trend, setup_type, mistakes, ai_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
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
        ))
        conn.commit()
        conn.close()

    @staticmethod
    def get_closed_trades(limit: int = 50, symbol: str = None):
        conn = _get_conn()
        query = "SELECT * FROM closed_trades"
        params = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY close_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    @staticmethod
    def get_stats():
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) FROM closed_trades WHERE realized_pnl != 0").fetchone()[0]
        if total == 0:
            conn.close()
            return {
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'win_rate': 0, 'total_pnl': 0.0, 'avg_profit': 0.0, 'avg_loss': 0.0,
                'best_trade': 0.0, 'best_trade_symbol': '',
                'worst_trade': 0.0, 'worst_trade_symbol': '',
                'unrealized_pnl': 0, 'open_positions': 0
            }
        pnl_sum = conn.execute("SELECT SUM(realized_pnl) FROM closed_trades WHERE realized_pnl != 0").fetchone()[0] or 0.0
        wins = conn.execute("SELECT COUNT(*) FROM closed_trades WHERE realized_pnl > 0").fetchone()[0]
        losses = conn.execute("SELECT COUNT(*) FROM closed_trades WHERE realized_pnl < 0").fetchone()[0]
        avg_profit = conn.execute("SELECT AVG(realized_pnl) FROM closed_trades WHERE realized_pnl > 0").fetchone()[0] or 0.0
        avg_loss = conn.execute("SELECT AVG(realized_pnl) FROM closed_trades WHERE realized_pnl < 0").fetchone()[0] or 0.0
        best = conn.execute("SELECT MAX(realized_pnl) FROM closed_trades WHERE realized_pnl != 0").fetchone()[0] or 0.0
        worst = conn.execute("SELECT MIN(realized_pnl) FROM closed_trades WHERE realized_pnl != 0").fetchone()[0] or 0.0
        best_row = conn.execute(
            "SELECT symbol FROM closed_trades WHERE realized_pnl = ? ORDER BY close_time DESC LIMIT 1",
            (best,)
        ).fetchone()
        worst_row = conn.execute(
            "SELECT symbol FROM closed_trades WHERE realized_pnl = ? ORDER BY close_time DESC LIMIT 1",
            (worst,)
        ).fetchone()
        unrealized = conn.execute("SELECT SUM(unrealized_pnl) FROM open_trades").fetchone()[0] or 0.0
        open_count = conn.execute("SELECT COUNT(*) FROM open_trades").fetchone()[0]
        conn.close()
        return {
            'total_trades': total,
            'winning_trades': wins,
            'losing_trades': losses,
            'win_rate': (wins / total) * 100 if total > 0 else 0,
            'total_pnl': pnl_sum,
            'avg_profit': avg_profit,
            'avg_loss': avg_loss,
            'best_trade': best,
            'best_trade_symbol': best_row['symbol'] if best_row else '',
            'worst_trade': worst,
            'worst_trade_symbol': worst_row['symbol'] if worst_row else '',
            'unrealized_pnl': unrealized,
            'open_positions': open_count
        }

    @staticmethod
    def add_comment(trade_id: int, comment: str):
        conn = _get_conn()
        conn.execute("UPDATE closed_trades SET exit_comment = ? WHERE id = ?", (comment, trade_id))
        conn.commit()
        conn.close()

    @staticmethod
    def find_trade_by_id(trade_id: int):
        conn = _get_conn()
        row = conn.execute("SELECT * FROM closed_trades WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def update_trade_metrics(trade_id: int, **kwargs):
        allowed = ['risk_percent', 'leverage', 'stop_loss', 'take_profit', 'risk_reward',
                   'entry_comment', 'exit_comment', 'ai_review',
                   'holding_minutes', 'btc_price', 'eth_price', 'market_trend',
                   'setup_type', 'mistakes', 'ai_score']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [trade_id]
        conn = _get_conn()
        conn.execute(f"UPDATE closed_trades SET {set_clause} WHERE id=?", values)
        conn.commit()
        conn.close()

    @staticmethod
    def get_last_closed_id():
        conn = _get_conn()
        row = conn.execute("SELECT MAX(id) FROM closed_trades").fetchone()
        conn.close()
        return row[0] if row else None