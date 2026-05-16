"""Performance metrics computed from a list of completed trades."""

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass
class PerformanceMetrics:
    total_trades:    int
    win_trades:      int
    loss_trades:     int
    win_rate:        float      # 0–1
    total_pnl:       float
    avg_win:         float
    avg_loss:        float
    profit_factor:   float      # gross wins / gross losses
    expectancy:      float      # avg P&L per trade
    max_drawdown:    float      # max peak-to-trough
    sharpe_ratio:    float | None
    best_trade:      float
    worst_trade:     float
    avg_holding_days: float | None

    def summary(self) -> str:
        return (
            f"Trades: {self.total_trades}  "
            f"WR: {self.win_rate*100:.1f}%  "
            f"PF: {self.profit_factor:.2f}  "
            f"Exp: ₹{self.expectancy:.0f}  "
            f"MDD: {self.max_drawdown*100:.1f}%  "
            f"P&L: ₹{self.total_pnl:.0f}"
        )


def compute_metrics(
    pnls: Sequence[float],
    holding_days: Sequence[float] | None = None,
    risk_free_rate: float = 0.07,
) -> PerformanceMetrics:
    if not pnls:
        return PerformanceMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, None, 0, 0, None)

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl    = sum(pnls)
    gross_wins   = sum(wins)
    gross_losses = abs(sum(losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    win_rate     = len(wins) / len(pnls)
    avg_win      = gross_wins / len(wins)   if wins   else 0.0
    avg_loss     = gross_losses / len(losses) if losses else 0.0
    expectancy   = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    # Max drawdown
    peak = 0.0
    cum  = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = (peak - cum) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    # Sharpe (daily returns, annualised assuming 252 trading days)
    n = len(pnls)
    sharpe = None
    if n >= 10:
        mean = sum(pnls) / n
        variance = sum((p - mean) ** 2 for p in pnls) / n
        std = math.sqrt(variance)
        if std > 0:
            daily_rf = risk_free_rate / 252
            sharpe = round(((mean - daily_rf) / std) * math.sqrt(252), 3)

    avg_holding = sum(holding_days) / len(holding_days) if holding_days else None

    return PerformanceMetrics(
        total_trades=n,
        win_trades=len(wins),
        loss_trades=len(losses),
        win_rate=round(win_rate, 4),
        total_pnl=round(total_pnl, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        profit_factor=round(profit_factor, 4),
        expectancy=round(expectancy, 2),
        max_drawdown=round(max_dd, 6),
        sharpe_ratio=sharpe,
        best_trade=round(max(pnls), 2),
        worst_trade=round(min(pnls), 2),
        avg_holding_days=round(avg_holding, 1) if avg_holding else None,
    )
