"""
Daily P&L tracker — reads portfolio snapshot and trade history,
persists EOD summary, triggers Telegram report.

Run once at 3:45 PM IST (after MIS square-off).
"""

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from local_db import get_portfolio_snapshot, get_all_trades
from monitoring.alerts import send_eod_report
from risk.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

EOD_DIR = Path("data/eod")
EOD_DIR.mkdir(parents=True, exist_ok=True)


def _get_snapshot() -> dict:
    try:
        return get_portfolio_snapshot()
    except Exception as exc:
        logger.warning("Could not fetch portfolio snapshot: %s", exc)
        return {}


def _get_today_trades() -> list[dict]:
    try:
        today = str(date.today())
        all_trades = get_all_trades(limit=500)
        return [
            t for t in all_trades
            if not t.get("is_open") and (t.get("executed_at") or "").startswith(today)
        ]
    except Exception as exc:
        logger.warning("Could not fetch today's trades: %s", exc)
        return []


async def run_eod() -> None:
    snap         = _get_snapshot()
    today_trades = _get_today_trades()
    cb           = CircuitBreaker()
    status       = cb.status_report()

    today        = str(date.today())
    daily_pnl    = snap.get("daily_pnl", 0.0)
    total_pnl    = sum(t.get("realized_pnl") or 0 for t in today_trades)
    total_trades = len(today_trades)
    win_trades   = sum(1 for t in today_trades if (t.get("realized_pnl") or 0) > 0)
    win_rate     = (win_trades / total_trades * 100) if total_trades > 0 else 0.0
    circuit      = status["state"]

    eod_record = {
        "date":         today,
        "daily_pnl":    daily_pnl,
        "total_trades": total_trades,
        "win_rate":     win_rate,
        "circuit_state": circuit,
        "drawdown_pct": status["drawdown_pct"],
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    (EOD_DIR / f"{today}.json").write_text(json.dumps(eod_record, indent=2))
    logger.info("EOD saved: P&L=₹%.0f trades=%d WR=%.1f%%", daily_pnl, total_trades, win_rate)

    await send_eod_report(
        daily_pnl=daily_pnl,
        realized_pnl=total_pnl,
        total_trades=total_trades,
        win_rate=win_rate,
        circuit_state=circuit,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run_eod())
