"""
ATR-based position sizer for all three trading tiers.
"""

import math
import logging
from dataclasses import dataclass

from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class SizingResult:
    qty: int
    risk_amount: float       # INR at risk
    stop_loss_price: float
    target_price: float
    notional: float          # qty × entry_price
    notional_pct: float      # notional / account_equity
    max_by_notional: int     # cap from max_position_pct
    max_by_risk: int         # cap from risk_pct
    final_qty: int           # min of both caps (same as qty)
    risk_reward: float


class PositionSizer:
    """
    Usage:
        sizer = PositionSizer(account_equity=500_000)
        result = sizer.equity("RELIANCE", entry=1250.0, atr=18.5)
    """

    def __init__(self, account_equity: float) -> None:
        self.account_equity = account_equity

    def _base(
        self,
        entry: float,
        atr: float,
        atr_stop_mult: float,
        atr_target_mult: float,
        max_risk_pct: float,
        max_notional_pct: float,
        min_qty: int = 1,
    ) -> SizingResult:
        # Stop distance = ATR × multiplier, floored at 2% of entry
        stop_dist = max(atr * atr_stop_mult, entry * 0.02)
        stop_loss = round(entry - stop_dist, 2)
        target    = round(entry + atr * atr_target_mult, 2)

        risk_amount = self.account_equity * max_risk_pct
        max_by_risk = math.floor(risk_amount / stop_dist) if stop_dist > 0 else min_qty

        max_by_notional = math.floor((self.account_equity * max_notional_pct) / entry)

        qty = max(min(max_by_risk, max_by_notional), min_qty)
        notional = round(qty * entry, 2)
        rr = round((target - entry) / stop_dist, 2) if stop_dist > 0 else 0.0

        return SizingResult(
            qty=qty,
            risk_amount=round(risk_amount, 2),
            stop_loss_price=stop_loss,
            target_price=target,
            notional=notional,
            notional_pct=round(notional / self.account_equity, 4),
            max_by_notional=max_by_notional,
            max_by_risk=max_by_risk,
            final_qty=qty,
            risk_reward=rr,
        )

    def equity(
        self,
        symbol: str,
        entry: float,
        atr: float,
        atr_stop_mult: float = 1.5,
        atr_target_mult: float = 2.5,
    ) -> SizingResult:
        """Standard equity position (Tier 1)."""
        result = self._base(
            entry=entry,
            atr=atr,
            atr_stop_mult=atr_stop_mult,
            atr_target_mult=atr_target_mult,
            max_risk_pct=settings.max_account_risk_pct,
            max_notional_pct=settings.max_single_stock_pct,
        )
        logger.info(
            "[EQUITY] %s qty=%d entry=%.2f SL=%.2f T=%.2f R:R=%.2f notional=%.0f",
            symbol, result.qty, entry, result.stop_loss_price, result.target_price,
            result.risk_reward, result.notional,
        )
        return result

    def penny(
        self,
        symbol: str,
        entry: float,
        atr: float | None = None,
    ) -> SizingResult:
        """
        Penny/SME stock sizing (Tier 3).
        If ATR not available, use fixed 15% stop as per CLAUDE.md rules.
        """
        if atr is None or atr <= 0:
            atr = entry * settings.penny_stop_loss_pct  # synthetic ATR = stop distance

        result = self._base(
            entry=entry,
            atr=atr,
            atr_stop_mult=1.0,       # stop = 1× ATR (= 15% for penny)
            atr_target_mult=2.5,     # target ~25% (lower bound)
            max_risk_pct=settings.max_account_risk_pct,
            max_notional_pct=settings.max_penny_stock_pct,
        )
        # Override stop to fixed 15% for penny stocks
        result = SizingResult(
            qty=result.qty,
            risk_amount=result.risk_amount,
            stop_loss_price=round(entry * (1 - settings.penny_stop_loss_pct), 2),
            target_price=round(entry * (1 + settings.penny_target_pct_low), 2),
            notional=result.notional,
            notional_pct=result.notional_pct,
            max_by_notional=result.max_by_notional,
            max_by_risk=result.max_by_risk,
            final_qty=result.qty,
            risk_reward=round(settings.penny_target_pct_low / settings.penny_stop_loss_pct, 2),
        )
        logger.info(
            "[PENNY]  %s qty=%d entry=%.2f SL=%.2f T=%.2f R:R=%.2f notional=%.0f",
            symbol, result.qty, entry, result.stop_loss_price, result.target_price,
            result.risk_reward, result.notional,
        )
        return result

    def options(
        self,
        symbol: str,
        premium: float,
        lot_size: int,
        num_lots: int = 1,
    ) -> dict:
        """
        F&O options sizing (Tier 2).
        Max loss = premium × lot_size × num_lots (defined risk).
        Ensures position ≤ max_fno_pct of portfolio.
        """
        cost_per_lot = premium * lot_size
        max_notional = self.account_equity * settings.max_fno_pct
        max_lots = math.floor(max_notional / cost_per_lot) if cost_per_lot > 0 else 1
        final_lots = max(1, min(num_lots, max_lots))
        total_premium = round(final_lots * cost_per_lot, 2)
        exit_at_loss = round(total_premium * settings.option_max_loss_pct, 2)

        result = {
            "symbol":         symbol,
            "lots":           final_lots,
            "lot_size":       lot_size,
            "qty":            final_lots * lot_size,
            "premium":        round(premium, 2),
            "total_cost":     total_premium,
            "max_loss":       total_premium,            # defined risk
            "exit_at_loss":   exit_at_loss,             # exit if premium falls by 50%
            "exit_price":     round(premium * (1 - settings.option_max_loss_pct), 2),
            "notional_pct":   round(total_premium / self.account_equity, 4),
        }
        logger.info(
            "[F&O]    %s lots=%d premium=%.2f total_cost=%.0f max_loss=%.0f",
            symbol, final_lots, premium, total_premium, total_premium,
        )
        return result

    def update_equity(self, new_equity: float) -> None:
        self.account_equity = new_equity
