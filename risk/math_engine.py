"""
Trading Math Engine — implements The Math of Trading framework.

Formulas:
  EV       = (win_rate × avg_win) - (loss_rate × avg_loss)
  Kelly %  = (win_rate × avg_win - loss_rate × avg_loss) / (avg_win × avg_loss)
  Half-Kelly (default) = Kelly % / 2   [more conservative, standard for live trading]
  Break-even = loss_pct / (1 - loss_pct)
  Risk of Ruin = ((1 - edge) / (1 + edge)) ^ n_units_of_capital
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# Risk of Ruin thresholds per the Shmuts framework
ROR_PROFESSIONAL = 0.01   # < 1%: professional standard
ROR_ACCEPTABLE   = 0.05   # < 5%: acceptable with caution
ROR_DANGER       = 0.10   # > 10%: unacceptable — halt trading

# Sample size thresholds for statistical confidence
MIN_TRADES_CONFIDENCE = 30    # below this: INSUFFICIENT_DATA
LOW_CONFIDENCE_TRADES = 300   # below this: LOW_CONFIDENCE (need more data)


@dataclass
class EVResult:
    win_rate: float
    loss_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    expected_value: float        # EV as a fraction (e.g. 0.008 = 0.8% per trade)
    kelly_fraction: float        # full Kelly %
    half_kelly_fraction: float   # recommended: half-Kelly
    has_positive_edge: bool
    break_even_required: float   # % gain needed to recover a loss of avg_loss_pct
    sample_size: int
    confidence_level: Literal["SUFFICIENT", "LOW_CONFIDENCE", "INSUFFICIENT_DATA"]
    warnings: list[str] = field(default_factory=list)


@dataclass
class RiskOfRuinResult:
    risk_of_ruin: float          # probability of total ruin (0-1)
    status: Literal["SAFE", "CAUTION", "DANGER", "HALT"]
    kelly_fraction: float
    n_trades_analyzed: int
    recommended_position_pct: float   # suggested % of capital per trade
    message: str


@dataclass
class StrategyStats:
    strategy_name: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    total_pnl: float
    ev: EVResult | None
    ror: RiskOfRuinResult | None
    data_source: Literal["supabase", "empty"]


class TradingMathEngine:
    """
    Stateless math engine — all methods are pure functions.
    Call get_strategy_statistics() to pull live data from Supabase.
    """

    def calculate_expected_value(
        self,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        sample_size: int = 0,
    ) -> EVResult:
        """
        EV = (win_rate × avg_win) − (loss_rate × avg_loss)
        Kelly % = EV / avg_win  [simplified form when avg_win and avg_loss differ]
        Full formula: K = (win_rate × avg_win − loss_rate × avg_loss) / (avg_win × avg_loss)
        """
        if avg_win_pct <= 0:
            avg_win_pct = 0.0001
        if avg_loss_pct <= 0:
            avg_loss_pct = 0.0001

        loss_rate = 1 - win_rate
        ev = (win_rate * avg_win_pct) - (loss_rate * avg_loss_pct)
        kelly = (win_rate * avg_win_pct - loss_rate * avg_loss_pct) / (avg_win_pct * avg_loss_pct)
        half_kelly = kelly / 2
        break_even = avg_loss_pct / (1 - avg_loss_pct) if avg_loss_pct < 1 else float("inf")

        # Clamp Kelly to sane range
        half_kelly = max(0.0, min(half_kelly, 0.25))  # never risk > 25% per trade

        warnings: list[str] = []
        if win_rate < 0.35:
            warnings.append(f"Win rate {win_rate:.0%} is very low — requires large avg_win to be +EV")
        if avg_win_pct < avg_loss_pct:
            warnings.append("Avg win < avg loss — need win rate >50% to be +EV")
        if ev <= 0:
            warnings.append("NEGATIVE EXPECTED VALUE — do not trade this setup")
        if half_kelly < 0.005:
            warnings.append("Half-Kelly < 0.5% — position size too small to be meaningful")

        if sample_size < MIN_TRADES_CONFIDENCE:
            conf: Literal["SUFFICIENT", "LOW_CONFIDENCE", "INSUFFICIENT_DATA"] = "INSUFFICIENT_DATA"
            warnings.append(f"Only {sample_size} trades — need ≥{MIN_TRADES_CONFIDENCE} for any confidence")
        elif sample_size < LOW_CONFIDENCE_TRADES:
            conf = "LOW_CONFIDENCE"
            warnings.append(f"{sample_size} trades — need ≥{LOW_CONFIDENCE_TRADES} for 95% statistical confidence")
        else:
            conf = "SUFFICIENT"

        return EVResult(
            win_rate=round(win_rate, 4),
            loss_rate=round(loss_rate, 4),
            avg_win_pct=round(avg_win_pct, 6),
            avg_loss_pct=round(avg_loss_pct, 6),
            expected_value=round(ev, 6),
            kelly_fraction=round(max(0, kelly), 6),
            half_kelly_fraction=round(half_kelly, 6),
            has_positive_edge=ev > 0,
            break_even_required=round(break_even, 6),
            sample_size=sample_size,
            confidence_level=conf,
            warnings=warnings,
        )

    def calculate_risk_of_ruin(
        self,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        risk_per_trade_pct: float,
        n_trades: int = 100,
    ) -> RiskOfRuinResult:
        """
        Risk of Ruin using the standard formula:
          edge = (win_rate × avg_win - loss_rate × avg_loss) / (win_rate × avg_win + loss_rate × avg_loss)
          RoR ≈ ((1 - edge) / (1 + edge)) ^ (1 / risk_per_trade_pct)

        This approximates ruin as "losing all capital" via a geometric random walk.
        """
        loss_rate = 1 - win_rate
        ev = (win_rate * avg_win_pct) - (loss_rate * avg_loss_pct)
        gross = (win_rate * avg_win_pct) + (loss_rate * avg_loss_pct)

        if gross <= 0 or risk_per_trade_pct <= 0:
            return RiskOfRuinResult(
                risk_of_ruin=1.0,
                status="HALT",
                kelly_fraction=0.0,
                n_trades_analyzed=n_trades,
                recommended_position_pct=0.01,
                message="Cannot compute RoR with zero or negative inputs — halt trading",
            )

        edge = ev / gross

        # RoR formula
        if edge <= 0:
            ror = 1.0  # negative edge = certain ruin eventually
        elif edge >= 1:
            ror = 0.0
        else:
            base = (1 - edge) / (1 + edge)
            exponent = 1.0 / risk_per_trade_pct
            ror = base ** exponent

        ror = max(0.0, min(1.0, ror))

        # Kelly for recommended position size
        kelly_full = (win_rate * avg_win_pct - loss_rate * avg_loss_pct) / (avg_win_pct * avg_loss_pct)
        half_kelly = max(0.0, min(kelly_full / 2, 0.25))

        # Determine status and recommendation
        if ror < ROR_PROFESSIONAL:
            status: Literal["SAFE", "CAUTION", "DANGER", "HALT"] = "SAFE"
            recommended = risk_per_trade_pct  # no change needed
            message = f"Risk of Ruin {ror:.2%} — within professional standard (<1%). Strategy is sound."
        elif ror < ROR_ACCEPTABLE:
            status = "CAUTION"
            recommended = risk_per_trade_pct * 0.5  # halve position size
            message = (
                f"Risk of Ruin {ror:.2%} — acceptable but elevated (1-5%). "
                "Reduce position size by 50%."
            )
        elif ror < ROR_DANGER:
            status = "DANGER"
            recommended = risk_per_trade_pct * 0.25
            message = (
                f"Risk of Ruin {ror:.2%} — high risk (5-10%). "
                "Reduce position size by 75% or improve win rate/R:R before trading live."
            )
        else:
            status = "HALT"
            recommended = 0.005  # floor at 0.5%
            message = (
                f"Risk of Ruin {ror:.2%} — UNACCEPTABLE (>10%). "
                "HALT all new trades. Fix strategy edge before resuming."
            )

        return RiskOfRuinResult(
            risk_of_ruin=round(ror, 6),
            status=status,
            kelly_fraction=round(half_kelly, 6),
            n_trades_analyzed=n_trades,
            recommended_position_pct=round(min(recommended, half_kelly), 6),
            message=message,
        )

    def calculate_break_even_required(self, loss_pct: float) -> float:
        """% gain needed to recover a loss of loss_pct. E.g. 25% loss needs 33.3% gain."""
        if loss_pct >= 1.0:
            return float("inf")
        return round(loss_pct / (1 - loss_pct), 6)

    def get_strategy_statistics(
        self,
        strategy_name: str | None = None,
        days: int = 90,
    ) -> StrategyStats:
        """
        Query Supabase trade_journal for win rate, avg win/loss, EV, Kelly, RoR.
        Falls back to trades table if trade_journal is empty.
        """
        try:
            from supabase_client import db
            from datetime import datetime, timezone, timedelta

            cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()

            query = db.table("trade_journal").select("*").gte("created_at", cutoff)
            if strategy_name:
                query = query.ilike("strategy_votes", f"%{strategy_name}%")

            result = query.order("created_at", desc=True).execute()
            rows = result.data or []

            if not rows:
                # Fallback to trades table
                tq = db.table("trades").select("*").eq("is_open", False).gte("executed_at", cutoff)
                tres = tq.execute()
                rows = _normalize_trades_to_journal(tres.data or [])

            return _compute_stats(strategy_name or "ALL", rows)

        except Exception as exc:
            logger.warning("Supabase stats query failed: %s", exc)
            return StrategyStats(
                strategy_name=strategy_name or "ALL",
                total_trades=0,
                wins=0,
                losses=0,
                win_rate=0.0,
                avg_win_pct=0.0,
                avg_loss_pct=0.0,
                total_pnl=0.0,
                ev=None,
                ror=None,
                data_source="empty",
            )

    def validate_strategy_edge(self, stats: StrategyStats) -> dict:
        """
        High-level verdict: is this strategy worth trading based on math?
        Returns a dict suitable for MCP tool output or Telegram summary.
        """
        if stats.total_trades < MIN_TRADES_CONFIDENCE:
            return {
                "verdict": "INSUFFICIENT_DATA",
                "message": (
                    f"Only {stats.total_trades} closed trades on record. "
                    f"Need ≥{MIN_TRADES_CONFIDENCE} for basic confidence, "
                    f"≥{LOW_CONFIDENCE_TRADES} for 95% statistical confidence."
                ),
                "recommendation": "Keep trading the strategy mechanically and track all outcomes.",
            }

        ev = stats.ev
        ror = stats.ror

        if ev is None or not ev.has_positive_edge:
            return {
                "verdict": "NEGATIVE_EDGE",
                "message": f"Strategy has negative EV ({ev.expected_value:.4f} if ev else 'unknown'}). Do not trade.",
                "recommendation": "Review entry criteria, improve R:R ratio, or abandon this strategy.",
            }

        ror_status = ror.status if ror else "UNKNOWN"
        return {
            "verdict": "VALID_EDGE" if ror_status in ("SAFE", "CAUTION") else "RISKY_EDGE",
            "win_rate": f"{stats.win_rate:.1%}",
            "avg_win": f"{stats.avg_win_pct:.2%}",
            "avg_loss": f"{stats.avg_loss_pct:.2%}",
            "expected_value_per_trade": f"{ev.expected_value:.4f}",
            "half_kelly_position_size": f"{ev.half_kelly_fraction:.2%}",
            "risk_of_ruin": f"{ror.risk_of_ruin:.2%}" if ror else "N/A",
            "ror_status": ror_status,
            "confidence": ev.confidence_level,
            "sample_size": stats.total_trades,
            "warnings": ev.warnings,
            "message": ror.message if ror else "Unable to compute RoR",
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _compute_stats(name: str, rows: list[dict]) -> StrategyStats:
    engine = TradingMathEngine()
    wins = [r for r in rows if r.get("outcome") == "WIN"]
    losses = [r for r in rows if r.get("outcome") == "LOSS"]
    total = len(rows)

    if total == 0:
        return StrategyStats(
            strategy_name=name, total_trades=0, wins=0, losses=0,
            win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
            total_pnl=0.0, ev=None, ror=None, data_source="empty",
        )

    win_rate = len(wins) / total
    avg_win = sum(float(r.get("final_pnl_pct", 0)) for r in wins) / max(len(wins), 1)
    avg_loss = abs(sum(float(r.get("final_pnl_pct", 0)) for r in losses) / max(len(losses), 1))
    total_pnl = sum(float(r.get("final_pnl_pct", 0)) for r in rows)

    ev = engine.calculate_expected_value(win_rate, avg_win, avg_loss, sample_size=total)
    ror = engine.calculate_risk_of_ruin(
        win_rate, avg_win, avg_loss,
        risk_per_trade_pct=ev.half_kelly_fraction or 0.02,
        n_trades=total,
    )

    return StrategyStats(
        strategy_name=name,
        total_trades=total,
        wins=len(wins),
        losses=len(losses),
        win_rate=round(win_rate, 4),
        avg_win_pct=round(avg_win, 6),
        avg_loss_pct=round(avg_loss, 6),
        total_pnl=round(total_pnl, 4),
        ev=ev,
        ror=ror,
        data_source="supabase",
    )


def _normalize_trades_to_journal(trades: list[dict]) -> list[dict]:
    """Convert trades table rows into trade_journal format for stats computation."""
    rows = []
    for t in trades:
        entry = float(t.get("entry_price") or 0)
        exit_ = float(t.get("exit_price") or 0)
        if entry <= 0:
            continue
        pnl_pct = (exit_ - entry) / entry
        outcome = "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BREAKEVEN")
        rows.append({
            "outcome": outcome,
            "final_pnl_pct": pnl_pct,
            "strategy_votes": t.get("tag", ""),
            "created_at": t.get("executed_at", ""),
        })
    return rows
