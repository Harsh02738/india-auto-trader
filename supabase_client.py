"""
Shared Supabase client for Python-side code (data collectors, MCP server, monitoring).
Usage:
    from supabase_client import db
    db.table("signals").insert({...}).execute()
"""

import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

_url  = os.environ["SUPABASE_URL"]
_key  = os.environ["SUPABASE_KEY"]

db: Client = create_client(_url, _key)


# ── Convenience helpers ────────────────────────────────────────────────────────

def upsert_signal(signal: dict) -> dict:
    """Write a signal to Supabase, updating if same symbol+created_at exists."""
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
        "executed":           signal.get("executed", False),
    }
    result = db.table("signals").insert(payload).execute()
    return result.data[0] if result.data else {}


def record_trade(trade: dict) -> dict:
    """Insert a new trade when an order is placed."""
    result = db.table("trades").insert(trade).execute()
    return result.data[0] if result.data else {}


def close_trade(order_id: str, exit_price: float, realized_pnl: float) -> dict:
    """Mark a trade as closed with exit price and P&L."""
    from datetime import datetime, timezone
    result = (
        db.table("trades")
        .update({
            "is_open":      False,
            "exit_price":   exit_price,
            "realized_pnl": realized_pnl,
            "closed_at":    datetime.now(tz=timezone.utc).isoformat(),
        })
        .eq("order_id", order_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def upsert_portfolio_snapshot(snapshot: dict) -> dict:
    """Upsert today's portfolio snapshot (one row per day)."""
    from datetime import date
    payload = {**snapshot, "snapshot_date": str(date.today())}
    result = (
        db.table("portfolio_snapshots")
        .upsert(payload, on_conflict="snapshot_date")
        .execute()
    )
    return result.data[0] if result.data else {}


def get_open_trades() -> list[dict]:
    result = db.table("trades").select("*").eq("is_open", True).execute()
    return result.data or []


def get_latest_signals(limit: int = 20) -> list[dict]:
    result = (
        db.table("signals")
        .select("*")
        .order("composite_score", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
