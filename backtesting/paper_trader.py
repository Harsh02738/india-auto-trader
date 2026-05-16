"""
Paper trading simulator.
Reads signal files, simulates execution, tracks virtual P&L.
Run continuously during market hours to validate strategy before going live.

Usage:
  python -m backtesting.paper_trader --capital 500000
"""

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backtesting.metrics import compute_metrics
from config.settings import settings

logger = logging.getLogger(__name__)

PAPER_DIR = Path("data/paper")
PAPER_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PaperPosition:
    symbol:         str
    tier:           str
    action:         str
    qty:            int
    entry_price:    float
    stop_loss:      float
    target:         float
    composite_score: float
    entered_at:     str
    product:        str


@dataclass
class PaperState:
    capital:        float
    cash:           float
    positions:      list[PaperPosition] = field(default_factory=list)
    closed_trades:  list[dict] = field(default_factory=list)
    consecutive_losses: int = 0
    peak_equity:    float = 0.0


class PaperTrader:
    def __init__(self, capital: float = 500_000.0):
        state_file = PAPER_DIR / "state.json"
        if state_file.exists():
            saved = json.loads(state_file.read_text())
            self.state = PaperState(
                capital=saved["capital"],
                cash=saved["cash"],
                positions=[PaperPosition(**p) for p in saved["positions"]],
                closed_trades=saved["closed_trades"],
                consecutive_losses=saved.get("consecutive_losses", 0),
                peak_equity=saved.get("peak_equity", capital),
            )
        else:
            self.state = PaperState(capital=capital, cash=capital, peak_equity=capital)

        self.state_file = state_file

    def _save(self) -> None:
        self.state_file.write_text(json.dumps({
            "capital":           self.state.capital,
            "cash":              self.state.cash,
            "positions":         [vars(p) for p in self.state.positions],
            "closed_trades":     self.state.closed_trades,
            "consecutive_losses": self.state.consecutive_losses,
            "peak_equity":       self.state.peak_equity,
        }, indent=2))

    def _equity(self) -> float:
        pos_value = sum(p.qty * p.entry_price for p in self.state.positions)
        return self.state.cash + pos_value

    def _circuit_ok(self) -> bool:
        equity = self._equity()
        daily_pnl_pct = (equity - self.state.capital) / self.state.capital
        return (
            daily_pnl_pct > -settings.circuit_daily_loss_pct
            and self.state.consecutive_losses < settings.circuit_consecutive_losses
        )

    def process_signal(self, signal: dict) -> None:
        symbol = signal.get("symbol", "")
        action = signal.get("action", "")
        score  = signal.get("composite_score", 0.0)
        tier   = signal.get("tier", "EQUITY")

        min_score = settings.min_fno_confidence if tier == "FNO" else settings.min_signal_confidence
        if score < min_score:
            return
        if not self._circuit_ok():
            logger.warning("Circuit open — skipping %s", symbol)
            return
        if any(p.symbol == symbol for p in self.state.positions):
            return  # already in this symbol

        entry  = signal.get("entry_price", 0.0)
        sl     = signal.get("stop_loss", entry * 0.97)
        target = signal.get("target", entry * 1.03)
        qty    = signal.get("quantity", 1)
        cost   = qty * entry

        if cost > self.state.cash * 0.95:
            return  # not enough cash

        self.state.cash -= cost
        self.state.positions.append(PaperPosition(
            symbol=symbol, tier=tier, action=action,
            qty=qty, entry_price=entry, stop_loss=sl, target=target,
            composite_score=score, product=signal.get("product", "MIS"),
            entered_at=datetime.now(tz=timezone.utc).isoformat(),
        ))
        logger.info("[PAPER BUY]  %s qty=%d entry=%.2f SL=%.2f T=%.2f", symbol, qty, entry, sl, target)
        self._save()

    def mark_to_market(self, symbol: str, current_price: float) -> None:
        """Check stop-loss / target hit for open positions."""
        for pos in list(self.state.positions):
            if pos.symbol != symbol:
                continue

            hit_sl = current_price <= pos.stop_loss
            hit_t  = current_price >= pos.target

            if hit_sl or hit_t:
                exit_price = pos.stop_loss if hit_sl else pos.target
                pnl = (exit_price - pos.entry_price) * pos.qty
                self.state.cash += pos.qty * exit_price

                if pnl < 0:
                    self.state.consecutive_losses += 1
                else:
                    self.state.consecutive_losses = 0

                reason = "SL_HIT" if hit_sl else "TARGET_HIT"
                logger.info("[PAPER %s] %s pnl=₹%.0f", reason, symbol, pnl)

                self.state.closed_trades.append({
                    "symbol":      symbol,
                    "tier":        pos.tier,
                    "entry":       pos.entry_price,
                    "exit":        exit_price,
                    "qty":         pos.qty,
                    "pnl":         round(pnl, 2),
                    "reason":      reason,
                    "entered_at":  pos.entered_at,
                    "closed_at":   datetime.now(tz=timezone.utc).isoformat(),
                })
                self.state.positions.remove(pos)
                self._save()

    def performance_report(self) -> dict:
        pnls = [t["pnl"] for t in self.state.closed_trades]
        metrics = compute_metrics(pnls)
        equity = self._equity()
        report = {
            "timestamp":   datetime.now(tz=timezone.utc).isoformat(),
            "equity":      round(equity, 2),
            "cash":        round(self.state.cash, 2),
            "open_positions": len(self.state.positions),
            "total_pnl":   round(equity - self.state.capital, 2),
            "metrics":     vars(metrics),
        }
        (PAPER_DIR / "performance.json").write_text(json.dumps(report, indent=2))
        logger.info("Paper trading: equity=₹%.0f | %s", equity, metrics.summary())
        return report

    def run_cycle(self) -> None:
        """One cycle: read all signal files and process."""
        signal_dir = Path("data/signals")
        for sig_file in signal_dir.glob("*_signal.json"):
            try:
                sig = json.loads(sig_file.read_text())
                if not sig.get("executed", False):
                    self.process_signal(sig)
            except Exception as exc:
                logger.error("Signal read error %s: %s", sig_file, exc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=500_000.0)
    parser.add_argument("--interval", type=int, default=60, help="Seconds between cycles")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s: %(message)s")
    trader = PaperTrader(capital=args.capital)
    logger.info("Paper trader started with ₹%.0f capital", args.capital)

    while True:
        try:
            trader.run_cycle()
            trader.performance_report()
        except Exception as exc:
            logger.error("Paper trader error: %s", exc)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
