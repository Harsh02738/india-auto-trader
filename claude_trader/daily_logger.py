"""
Daily decision + P&L logger for the Claude Trader system.

Writes one JSON line per analysis event to decisions_{date}.jsonl.
Writes a daily EOD summary to eod_{date}.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST      = ZoneInfo("Asia/Kolkata")
LOG_DIR  = Path("data/claude_trader")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _now_str() -> str:
    return datetime.now(IST).strftime("%H:%M IST")


def log_analysis(
    symbol: str,
    action: str,
    confidence: float,
    entry: float,
    stop_loss: float,
    target: float,
    reasoning: str,
    strategy_votes: str,
    executed: bool,
    chart_path: str = "",
) -> None:
    record = {
        "timestamp": _now_str(),
        "symbol": symbol,
        "action": action,
        "confidence": round(confidence, 3),
        "entry": entry,
        "stop_loss": stop_loss,
        "target": target,
        "reasoning": reasoning,
        "strategy_votes": strategy_votes,
        "executed": executed,
        "chart_path": chart_path,
    }
    path = LOG_DIR / f"decisions_{_today()}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    logger.debug("[Logger] %s %s conf=%.2f executed=%s", symbol, action, confidence, executed)


def log_trade(
    symbol: str,
    action: str,
    entry: float,
    stop_loss: float,
    target: float,
    sizing,        # SizingResult from PositionSizer
    reasoning: str,
) -> None:
    record = {
        "timestamp": _now_str(),
        "type": "TRADE_ENTRY",
        "symbol": symbol,
        "action": action,
        "entry": entry,
        "stop_loss": stop_loss,
        "target": target,
        "qty": sizing.qty if hasattr(sizing, "qty") else sizing.get("qty"),
        "risk_amount": sizing.risk_amount if hasattr(sizing, "risk_amount") else sizing.get("risk_amount"),
        "reasoning": reasoning,
    }
    path = LOG_DIR / f"decisions_{_today()}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def write_eod_report(
    symbols_scanned: list[str],
    scan_cycles: int,
    signals_fired: int,
    trades_executed: int,
    pnl_inr: float,
    api_calls: int = 0,
    api_cost_usd: float = 0.0,
) -> dict:
    win_rate = 0.0
    decisions_path = LOG_DIR / f"decisions_{_today()}.jsonl"
    if decisions_path.exists():
        trades = [
            json.loads(l) for l in decisions_path.read_text().splitlines()
            if l and json.loads(l).get("type") == "TRADE_ENTRY"
        ]
        if trades:
            # approximate: can be enriched later with exit data
            win_rate = 0.0

    report = {
        "date": _today(),
        "symbols_scanned": symbols_scanned,
        "total_scan_cycles": scan_cycles,
        "signals_fired": signals_fired,
        "trades_executed": trades_executed,
        "pnl_inr": round(pnl_inr, 2),
        "win_rate": round(win_rate, 3),
        "api_calls_used": api_calls,
        "api_cost_usd": round(api_cost_usd, 2),
    }
    eod_path = LOG_DIR / f"eod_{_today()}.json"
    eod_path.write_text(json.dumps(report, indent=2))
    logger.info("[Logger] EOD report written: %s", eod_path)
    return report


def track_api_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    usage_path = LOG_DIR / "api_usage.json"
    today = _today()
    data: dict = {}
    if usage_path.exists():
        try:
            data = json.loads(usage_path.read_text())
        except Exception:
            pass

    day = data.setdefault(today, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    day["calls"] += 1
    day["input_tokens"] += input_tokens
    day["output_tokens"] += output_tokens
    # Rough cost: Sonnet 4.6 = $3/MTok input, $15/MTok output
    day["cost_usd"] += round((input_tokens * 3 + output_tokens * 15) / 1_000_000, 5)

    usage_path.write_text(json.dumps(data, indent=2))
