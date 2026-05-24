import asyncio
from fastapi import APIRouter
from local_db import get_all_trades, get_portfolio_snapshot

router = APIRouter(prefix="/pnl", tags=["pnl"])


@router.get("/summary")
async def pnl_summary():
    loop = asyncio.get_event_loop()
    trades = await loop.run_in_executor(None, lambda: get_all_trades(limit=1000))
    snap   = await loop.run_in_executor(None, get_portfolio_snapshot)

    closed = [t for t in trades if not t.get("is_open")]
    open_  = [t for t in trades if t.get("is_open")]

    realized_pnl = sum(float(t.get("realized_pnl") or 0) for t in closed)
    wins = [t for t in closed if (t.get("realized_pnl") or 0) > 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0.0

    return {
        "total_trades":  len(trades),
        "closed_trades": len(closed),
        "open_trades":   len(open_),
        "realized_pnl":  round(realized_pnl, 2),
        "win_rate":      win_rate,
        "wins":          len(wins),
        "losses":        len(closed) - len(wins),
        "daily_pnl":     snap.get("daily_pnl", 0) if snap else 0,
        "circuit_state": snap.get("circuit_state", "SAFE") if snap else "SAFE",
    }
