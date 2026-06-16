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
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
            entry_price REAL NOT NULL,
            quantity REAL NOT NULL,
            leverage REAL DEFAULT 1,
            unrealized_pnl REAL DEFAULT 0,
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
            closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_closed_symbol ON closed_trades(symbol);
        CREATE INDEX IF NOT EXISTS idx_closed_date ON closed_trades(closed_at);
    """)
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
            INSERT INTO open_trades (symbol, side, entry_price, quantity, leverage, unrealized_pnl)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (trade['symbol'], trade['side'], trade['entry_price'],
              trade['quantity'], trade.get('leverage', 1), trade.get('unrealized_pnl', 0)))
        conn.commit()
        conn.close()

    @staticmethod
    def update_open_trade(symbol: str, **kwargs):
        allowed = ['unrealized_pnl', 'leverage', 'quantity', 'entry_price']
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
    def delete_open_trade(symbol: str):
        conn = _get_conn()
        conn.execute("DELETE FROM open_trades WHERE symbol=?", (symbol,))
        conn.commit()
        conn.close()

    @staticmethod
    def add_closed_trade(trade: dict):
        conn = _get_conn()
        conn.execute("""
            INSERT INTO closed_trades (symbol, side, entry_price, exit_price, quantity, realized_pnl, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trade['symbol'], trade['side'], trade['entry_price'],
              trade['exit_price'], trade['quantity'], trade['realized_pnl'], trade.get('comment', '')))
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
        query += " ORDER BY closed_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    @staticmethod
    def get_stats():
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) FROM closed_trades").fetchone()[0]
        if total == 0:
            conn.close()
            return {'total_trades': 0, 'win_rate': 0, 'total_pnl': 0.0,
                    'avg_profit': 0.0, 'avg_loss': 0.0, 'best_trade': 0.0, 'worst_trade': 0.0}
        pnl_sum = conn.execute("SELECT SUM(realized_pnl) FROM closed_trades").fetchone()[0] or 0.0
        wins = conn.execute("SELECT COUNT(*) FROM closed_trades WHERE realized_pnl > 0").fetchone()[0]
        losses = conn.execute("SELECT COUNT(*) FROM closed_trades WHERE realized_pnl < 0").fetchone()[0]
        avg_profit = conn.execute("SELECT AVG(realized_pnl) FROM closed_trades WHERE realized_pnl > 0").fetchone()[0] or 0.0
        avg_loss = conn.execute("SELECT AVG(realized_pnl) FROM closed_trades WHERE realized_pnl < 0").fetchone()[0] or 0.0
        best = conn.execute("SELECT MAX(realized_pnl) FROM closed_trades").fetchone()[0] or 0.0
        worst = conn.execute("SELECT MIN(realized_pnl) FROM closed_trades").fetchone()[0] or 0.0
        conn.close()
        return {
            'total_trades': total,
            'win_rate': (wins / total) * 100 if total > 0 else 0,
            'total_pnl': pnl_sum,
            'avg_profit': avg_profit,
            'avg_loss': avg_loss,
            'best_trade': best,
            'worst_trade': worst
        }

    @staticmethod
    def add_comment(trade_id: int, comment: str):
        conn = _get_conn()
        conn.execute("UPDATE closed_trades SET comment = ? WHERE id = ?", (comment, trade_id))
        conn.commit()
        conn.close()

    @staticmethod
    def find_trade_by_id(trade_id: int):
        conn = _get_conn()
        row = conn.execute("SELECT * FROM closed_trades WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        return dict(row) if row else None