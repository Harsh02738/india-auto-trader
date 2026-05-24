"""
Local SQLite storage — drop-in replacement for supabase_client.py.

All public function signatures are identical to supabase_client.py so that
callers can swap the import without any other changes.

Database: data/trading.db (path from settings.paper_db_path)
Tables  : trades, signals, portfolio_snapshots, trade_journal
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_DB_PATH = Path(settings.paper_db_path)
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()

# ── Schema creation ────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      TEXT    UNIQUE NOT NULL,
    symbol        TEXT    NOT NULL,
    tier          TEXT    DEFAULT 'EQUITY',
    action        TEXT    NOT NULL,
    product       TEXT    DEFAULT 'MIS',
    qty           INTEGER DEFAULT 0,
    entry_price   REAL    DEFAULT 0,
    exit_price    REAL,
    stop_loss     REAL    DEFAULT 0,
    target        REAL    DEFAULT 0,
    realized_pnl  REAL,
    is_open       INTEGER DEFAULT 1,
    composite_score REAL,
    confidence    REAL,
    reasoning     TEXT,
    tag           TEXT    DEFAULT 'PAPER',
    executed_at   TEXT    DEFAULT (datetime('now')),
    closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT    NOT NULL,
    tier                TEXT    DEFAULT 'EQUITY',
    action              TEXT,
    entry_price         REAL,
    stop_loss           REAL,
    target              REAL,
    quantity            INTEGER,
    composite_score     REAL,
    technical_score     REAL,
    fundamental_score   REAL,
    sentiment_score     REAL,
    news_score          REAL,
    confidence          TEXT,
    risk_reward         REAL,
    risk_amount_inr     REAL,
    reasoning           TEXT,
    earnings_within_days INTEGER,
    option_pcr          REAL,
    executed            INTEGER DEFAULT 0,
    created_at          TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date    TEXT    NOT NULL UNIQUE,
    account_equity   REAL,
    cash_available   REAL,
    open_positions   INTEGER DEFAULT 0,
    daily_pnl        REAL,
    realized_pnl_total REAL,
    circuit_state    TEXT    DEFAULT 'SAFE',
    circuit_reason   TEXT,
    consecutive_losses INTEGER DEFAULT 0,
    drawdown_pct     REAL,
    created_at       TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trade_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_order_id  TEXT,
    symbol          TEXT    NOT NULL,
    outcome         TEXT,
    final_pnl_pct   REAL,
    final_pnl_inr   REAL,
    strategy_votes  TEXT,
    entry_price     REAL,
    exit_price      REAL,
    lessons_text    TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    closed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol    ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_is_open   ON trades(is_open);
CREATE INDEX IF NOT EXISTS idx_signals_symbol   ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_created  ON signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_created  ON trade_journal(created_at DESC);
"""


def _conn() -> sqlite3.Connection:
    """Open a connection with row_factory for dict-like access."""
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _init():
    with _lock, _conn() as con:
        con.executescript(_DDL)


_init()


# ── Internal helpers ───────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    return dict(row)


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ── Signals ────────────────────────────────────────────────────────────────────

def upsert_signal(signal: dict) -> dict:
    payload = {
        "symbol":             signal.get("symbol"),
        "tier":               signal.get("tier", "EQUITY"),
        "action":             signal.get("action"),
        "entry_price":        signal.get("entry_price"),
        "stop_loss":          signal.get("stop_loss"),
        "target":             signal.get("target"),
        "quantity":           signal.get("quantity"),
        "composite_score":    signal.get("composite_score"),
        "technical_score":    signal.get("technical_score"),
        "fundamental_score":  signal.get("fundamental_score"),
        "sentiment_score":    signal.get("sentiment_score"),
        "news_score":         signal.get("news_score"),
        "confidence":         signal.get("confidence"),
        "risk_reward":        signal.get("risk_reward"),
        "risk_amount_inr":    signal.get("risk_amount_inr"),
        "reasoning":          signal.get("reasoning"),
        "earnings_within_days": signal.get("earnings_within_days"),
        "option_pcr":         signal.get("option_pcr"),
        "executed":           1 if signal.get("executed") else 0,
    }
    cols = ", ".join(payload.keys())
    placeholders = ", ".join("?" for _ in payload)
    sql = f"INSERT INTO signals ({cols}) VALUES ({placeholders})"
    with _lock, _conn() as con:
        cur = con.execute(sql, list(payload.values()))
        row = con.execute("SELECT * FROM signals WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_latest_signals(limit: int = 20) -> list[dict]:
    sql = "SELECT * FROM signals ORDER BY composite_score DESC LIMIT ?"
    with _lock, _conn() as con:
        rows = con.execute(sql, (limit,)).fetchall()
    return _rows_to_list(rows)


# ── Trades ─────────────────────────────────────────────────────────────────────

def record_trade(trade: dict) -> dict:
    payload = {
        "order_id":      trade.get("order_id"),
        "symbol":        trade.get("symbol"),
        "tier":          trade.get("tier", "EQUITY"),
        "action":        trade.get("action"),
        "product":       trade.get("product", "MIS"),
        "qty":           trade.get("qty", 0),
        "entry_price":   trade.get("entry_price"),
        "stop_loss":     trade.get("stop_loss", 0),
        "target":        trade.get("target", 0),
        "is_open":       1,
        "composite_score": trade.get("composite_score"),
        "confidence":    trade.get("confidence"),
        "reasoning":     trade.get("reasoning"),
        "tag":           trade.get("tag", "PAPER"),
    }
    cols = ", ".join(payload.keys())
    placeholders = ", ".join("?" for _ in payload)
    sql = f"INSERT OR IGNORE INTO trades ({cols}) VALUES ({placeholders})"
    with _lock, _conn() as con:
        cur = con.execute(sql, list(payload.values()))
        row = con.execute("SELECT * FROM trades WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def close_trade(order_id: str, exit_price: float, realized_pnl: float) -> dict:
    closed_at = datetime.now(tz=timezone.utc).isoformat()
    sql = """
        UPDATE trades
        SET is_open=0, exit_price=?, realized_pnl=?, closed_at=?
        WHERE order_id=?
    """
    with _lock, _conn() as con:
        con.execute(sql, (exit_price, realized_pnl, closed_at, order_id))
        row = con.execute("SELECT * FROM trades WHERE order_id=?", (order_id,)).fetchone()
    return _row_to_dict(row)


def get_open_trades() -> list[dict]:
    sql = "SELECT * FROM trades WHERE is_open=1"
    with _lock, _conn() as con:
        rows = con.execute(sql).fetchall()
    return _rows_to_list(rows)


def get_all_trades(limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?"
    with _lock, _conn() as con:
        rows = con.execute(sql, (limit,)).fetchall()
    return _rows_to_list(rows)


# ── Portfolio snapshots ────────────────────────────────────────────────────────

def upsert_portfolio_snapshot(snapshot: dict) -> dict:
    today = str(date.today())
    payload = {
        "snapshot_date":      today,
        "account_equity":     snapshot.get("account_equity"),
        "cash_available":     snapshot.get("cash_available"),
        "open_positions":     snapshot.get("open_positions", 0),
        "daily_pnl":          snapshot.get("daily_pnl"),
        "realized_pnl_total": snapshot.get("realized_pnl_total"),
        "circuit_state":      snapshot.get("circuit_state", "SAFE"),
        "circuit_reason":     snapshot.get("circuit_reason"),
        "consecutive_losses": snapshot.get("consecutive_losses", 0),
        "drawdown_pct":       snapshot.get("drawdown_pct"),
    }
    sql = """
        INSERT INTO portfolio_snapshots (snapshot_date, account_equity, cash_available,
            open_positions, daily_pnl, realized_pnl_total, circuit_state, circuit_reason,
            consecutive_losses, drawdown_pct)
        VALUES (:snapshot_date, :account_equity, :cash_available, :open_positions,
            :daily_pnl, :realized_pnl_total, :circuit_state, :circuit_reason,
            :consecutive_losses, :drawdown_pct)
        ON CONFLICT(snapshot_date) DO UPDATE SET
            account_equity=excluded.account_equity,
            cash_available=excluded.cash_available,
            open_positions=excluded.open_positions,
            daily_pnl=excluded.daily_pnl,
            realized_pnl_total=excluded.realized_pnl_total,
            circuit_state=excluded.circuit_state,
            circuit_reason=excluded.circuit_reason,
            consecutive_losses=excluded.consecutive_losses,
            drawdown_pct=excluded.drawdown_pct
    """
    with _lock, _conn() as con:
        con.execute(sql, payload)
        row = con.execute(
            "SELECT * FROM portfolio_snapshots WHERE snapshot_date=?", (today,)
        ).fetchone()
    return _row_to_dict(row)


def get_portfolio_snapshot() -> dict:
    today = str(date.today())
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT * FROM portfolio_snapshots WHERE snapshot_date=?", (today,)
        ).fetchone()
    return _row_to_dict(row)


# ── Trade journal (memory engineering) ────────────────────────────────────────

def insert_journal_entry(entry: dict) -> dict:
    payload = {
        "trade_order_id": entry.get("trade_order_id"),
        "symbol":         entry.get("symbol"),
        "outcome":        entry.get("outcome"),
        "final_pnl_pct":  entry.get("final_pnl_pct"),
        "final_pnl_inr":  entry.get("final_pnl_inr"),
        "strategy_votes": entry.get("strategy_votes"),
        "entry_price":    entry.get("entry_price"),
        "exit_price":     entry.get("exit_price"),
        "lessons_text":   entry.get("lessons_text"),
        "closed_at":      entry.get("closed_at", datetime.now(tz=timezone.utc).isoformat()),
    }
    cols = ", ".join(payload.keys())
    placeholders = ", ".join("?" for _ in payload)
    sql = f"INSERT INTO trade_journal ({cols}) VALUES ({placeholders})"
    with _lock, _conn() as con:
        cur = con.execute(sql, list(payload.values()))
        row = con.execute("SELECT * FROM trade_journal WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_journal_entries(
    strategy_name: str | None = None,
    days: int = 90,
    limit: int = 100,
) -> list[dict]:
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    if strategy_name:
        sql = """
            SELECT * FROM trade_journal
            WHERE created_at >= ?
              AND strategy_votes LIKE ?
            ORDER BY closed_at DESC LIMIT ?
        """
        params = (cutoff, f"%{strategy_name}%", limit)
    else:
        sql = "SELECT * FROM trade_journal WHERE created_at >= ? ORDER BY closed_at DESC LIMIT ?"
        params = (cutoff, limit)

    with _lock, _conn() as con:
        rows = con.execute(sql, params).fetchall()
    return _rows_to_list(rows)


# ── WebSocket broadcast helper ─────────────────────────────────────────────────
# Imported by backend/main.py to receive events from the trade engine.

_ws_listeners: list = []   # list of asyncio.Queue objects


def register_ws_listener(queue) -> None:
    _ws_listeners.append(queue)


def unregister_ws_listener(queue) -> None:
    if queue in _ws_listeners:
        _ws_listeners.remove(queue)


def broadcast_event(event_type: str, data: dict) -> None:
    """Push an event to all connected WebSocket listeners (non-blocking)."""
    if not _ws_listeners:
        return
    payload = json.dumps({"type": event_type, "data": data})
    for q in list(_ws_listeners):
        try:
            q.put_nowait(payload)
        except Exception:
            pass
