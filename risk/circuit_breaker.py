"""
Circuit breaker: halts all trading when risk limits are breached.

Three triggers (any one trips the breaker):
  1. Daily P&L loss > CIRCUIT_DAILY_LOSS_PCT of account equity
  2. N consecutive losing trades
  3. Portfolio drawdown from peak > CIRCUIT_MAX_DRAWDOWN_PCT

State is persisted in data/portfolio/snapshot.json so it survives restarts.
"""

import json
import logging
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = Path("data/portfolio/snapshot.json")
SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)


class CircuitState(StrEnum):
    SAFE     = "SAFE"
    WARNING  = "WARNING"   # approaching limits but not tripped
    TRIPPED  = "TRIPPED"   # all new entries blocked


def _load_snapshot() -> dict:
    if SNAPSHOT_PATH.exists():
        try:
            return json.loads(SNAPSHOT_PATH.read_text())
        except Exception:
            pass
    return _empty_snapshot()


def _empty_snapshot() -> dict:
    return {
        "date":                   str(date.today()),
        "circuit_state":          CircuitState.SAFE,
        "circuit_reason":         None,
        "account_equity":         0.0,
        "peak_equity":            0.0,
        "daily_pnl":              0.0,
        "daily_pnl_pct":          0.0,
        "consecutive_losses":     0,
        "drawdown_from_peak_pct": 0.0,
        "positions":              [],
        "today_trades":           [],
        "last_updated":           datetime.now(tz=timezone.utc).isoformat(),
    }


def _save_snapshot(snap: dict) -> None:
    snap["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    SNAPSHOT_PATH.write_text(json.dumps(snap, indent=2))


class CircuitBreaker:
    """
    Stateful circuit breaker.  Call update_pnl() after each trade;
    is_open() before any new order.
    """

    def __init__(self) -> None:
        self._snap = _load_snapshot()
        self._reset_daily_if_new_day()

    def _reset_daily_if_new_day(self) -> None:
        today = str(date.today())
        if self._snap.get("date") != today:
            # New trading day — reset daily counters but keep peak equity & state
            self._snap["date"] = today
            self._snap["daily_pnl"] = 0.0
            self._snap["daily_pnl_pct"] = 0.0
            self._snap["today_trades"] = []
            # Do NOT reset consecutive_losses — they persist across days
            # Do NOT reset circuit if tripped — requires manual reset
            _save_snapshot(self._snap)

    def _evaluate(self) -> CircuitState:
        equity  = self._snap.get("account_equity", 0.0)
        if equity <= 0:
            return CircuitState.SAFE

        daily_loss_pct = self._snap.get("daily_pnl_pct", 0.0)
        consec         = self._snap.get("consecutive_losses", 0)
        drawdown       = self._snap.get("drawdown_from_peak_pct", 0.0)

        # Hard trips
        if daily_loss_pct <= -settings.circuit_daily_loss_pct:
            return CircuitState.TRIPPED
        if consec >= settings.circuit_consecutive_losses:
            return CircuitState.TRIPPED
        if drawdown >= settings.circuit_max_drawdown_pct:
            return CircuitState.TRIPPED

        # Warning zone (80% of thresholds)
        warn_pct = 0.80
        if daily_loss_pct <= -settings.circuit_daily_loss_pct * warn_pct:
            return CircuitState.WARNING
        if consec >= int(settings.circuit_consecutive_losses * warn_pct):
            return CircuitState.WARNING
        if drawdown >= settings.circuit_max_drawdown_pct * warn_pct:
            return CircuitState.WARNING

        return CircuitState.SAFE

    @property
    def state(self) -> CircuitState:
        return CircuitState(self._snap.get("circuit_state", CircuitState.SAFE))

    def is_open(self) -> bool:
        """Returns True if trading is ALLOWED (circuit is not tripped)."""
        return self.state != CircuitState.TRIPPED

    def is_tripped(self) -> bool:
        return self.state == CircuitState.TRIPPED

    def update_equity(self, equity: float) -> None:
        """Call with current account equity (from get_limits())."""
        self._snap["account_equity"] = equity
        if equity > self._snap.get("peak_equity", 0.0):
            self._snap["peak_equity"] = equity

        peak = self._snap["peak_equity"]
        if peak > 0:
            drawdown = (peak - equity) / peak
            self._snap["drawdown_from_peak_pct"] = round(drawdown, 6)

        self._recompute()

    def record_trade(self, pnl: float) -> None:
        """Call after every completed trade with realized P&L."""
        self._snap["daily_pnl"] = round(self._snap.get("daily_pnl", 0.0) + pnl, 2)
        equity = self._snap.get("account_equity", 1.0)
        if equity > 0:
            self._snap["daily_pnl_pct"] = round(self._snap["daily_pnl"] / equity, 6)

        if pnl < 0:
            self._snap["consecutive_losses"] = self._snap.get("consecutive_losses", 0) + 1
        else:
            self._snap["consecutive_losses"] = 0  # reset streak on any win

        self._snap["today_trades"].append({
            "time": datetime.now(tz=timezone.utc).isoformat(),
            "pnl":  round(pnl, 2),
        })

        self._recompute()

    def _recompute(self) -> None:
        new_state = self._evaluate()
        old_state = self._snap.get("circuit_state", CircuitState.SAFE)

        if new_state == CircuitState.TRIPPED and old_state != CircuitState.TRIPPED:
            reason = self._trip_reason()
            logger.critical("CIRCUIT BREAKER TRIPPED: %s", reason)
            self._snap["circuit_reason"] = reason

        self._snap["circuit_state"] = new_state
        _save_snapshot(self._snap)

    def _trip_reason(self) -> str:
        daily_pct = self._snap.get("daily_pnl_pct", 0.0)
        consec    = self._snap.get("consecutive_losses", 0)
        drawdown  = self._snap.get("drawdown_from_peak_pct", 0.0)

        reasons = []
        if daily_pct <= -settings.circuit_daily_loss_pct:
            reasons.append(f"DAILY_LOSS={daily_pct*100:.1f}%")
        if consec >= settings.circuit_consecutive_losses:
            reasons.append(f"CONSECUTIVE_LOSSES={consec}")
        if drawdown >= settings.circuit_max_drawdown_pct:
            reasons.append(f"DRAWDOWN={drawdown*100:.1f}%")
        return " | ".join(reasons) if reasons else "UNKNOWN"

    def manual_reset(self, reason: str = "manual") -> None:
        """Manually reset a tripped circuit breaker (use with care)."""
        logger.warning("CIRCUIT BREAKER MANUALLY RESET by '%s'", reason)
        self._snap["circuit_state"] = CircuitState.SAFE
        self._snap["circuit_reason"] = None
        self._snap["consecutive_losses"] = 0
        _save_snapshot(self._snap)

    def status_report(self) -> dict:
        self._reset_daily_if_new_day()
        return {
            "state":                self._snap.get("circuit_state"),
            "reason":               self._snap.get("circuit_reason"),
            "daily_pnl":            self._snap.get("daily_pnl", 0.0),
            "daily_pnl_pct":        round(self._snap.get("daily_pnl_pct", 0.0) * 100, 2),
            "consecutive_losses":   self._snap.get("consecutive_losses", 0),
            "drawdown_pct":         round(self._snap.get("drawdown_from_peak_pct", 0.0) * 100, 2),
            "account_equity":       self._snap.get("account_equity", 0.0),
            "peak_equity":          self._snap.get("peak_equity", 0.0),
            "thresholds": {
                "daily_loss_pct":     settings.circuit_daily_loss_pct * 100,
                "consecutive_losses": settings.circuit_consecutive_losses,
                "max_drawdown_pct":   settings.circuit_max_drawdown_pct * 100,
            },
        }
