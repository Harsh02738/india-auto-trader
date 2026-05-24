"""
Opening Range Breakout (ORB) — most popular intraday strategy on NSE.

Edge: The high/low established in the first 15 minutes (9:15–9:30 AM) acts as
      a consensus price range set by early institutional orders. A breakout
      beyond this range confirmed by volume signals strong directional
      conviction. Target = 2× the opening range.

Signal: BUY when a 5-min candle closes above OR-high with volume ≥ 1.3×avg.
        SELL when it closes below OR-low. No entries after 14:00 IST.
        Stop = opposite side of the opening range.
"""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

from .base import BaseStrategy, StrategySignal

IST = ZoneInfo("Asia/Kolkata")
_ORB_CANDLES = 3          # first 3 × 5-min bars = 15-min opening range


class ORBStrategy(BaseStrategy):
    name = "ORB"
    timeframe = "intraday"

    ENTRY_CUTOFF_HOUR = 14   # no new entries after 14:00 IST
    MIN_VOL_RATIO = 1.3
    MIN_RANGE_PCT = 0.003    # OR must be at least 0.3% of price

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        candles = ohlcv.get("candles", [])
        if len(candles) < _ORB_CANDLES + 1:
            return self._hold(entry, f"Need ≥{_ORB_CANDLES + 1} intraday candles for ORB")

        now_ist = datetime.now(tz=IST)
        if now_ist.hour >= self.ENTRY_CUTOFF_HOUR:
            return self._hold(entry, "Past 14:00 IST — ORB entry window closed")

        atr = ohlcv.get("atr") or entry * 0.015
        vol_ratio = ohlcv.get("vol_ratio") or 1.0

        # Use pre-computed OR levels if available (set by kotak_realtime.py)
        or_high = ohlcv.get("or_high")
        or_low  = ohlcv.get("or_low")

        if or_high is None or or_low is None:
            opening = candles[:_ORB_CANDLES]
            or_high = max(c["h"] for c in opening)
            or_low  = min(c["l"] for c in opening)

        or_range = or_high - or_low
        if or_range <= 0 or (or_range / entry) < self.MIN_RANGE_PCT:
            return self._hold(entry, f"OR range too narrow ({or_range:.2f}) — unreliable ORB")

        vwap = ohlcv.get("vwap") or self._vwap(candles)
        prev_close = ohlcv.get("prev_day_close")

        # ── BUY: broke above OR-high ───────────────────────────────────────────
        if entry > or_high:
            if vol_ratio < self.MIN_VOL_RATIO:
                return self._hold(entry, f"ORB breakout without volume ({vol_ratio:.1f}x) — skip")

            drift_pct = (entry - or_high) / entry
            if drift_pct > 0.02:
                return self._hold(entry, f"Too far above OR-high ({drift_pct*100:.1f}%) — chasing")

            confidence = 0.0
            if vol_ratio >= 2.0:
                confidence += 0.35
            elif vol_ratio >= 1.5:
                confidence += 0.25
            else:
                confidence += 0.15

            if drift_pct < 0.005:
                confidence += 0.25   # fresh breakout

            if vwap > 0 and entry > vwap:
                confidence += 0.20

            if prev_close and candles[0].get("o", or_high) > prev_close * 1.003:
                confidence += 0.15   # gap-up + ORB = stronger

            confidence = self._clamp(confidence)
            stop_loss = round(or_low, 2)
            target    = round(entry + 2.0 * or_range, 2)
            rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

            return StrategySignal(
                action="BUY",
                confidence=confidence,
                entry=entry,
                stop_loss=stop_loss,
                target=target,
                risk_reward=rr,
                reasoning=(
                    f"ORB breakout: {entry:.2f} > OR-high {or_high:.2f} "
                    f"(range={or_range:.2f}), vol={vol_ratio:.1f}x, "
                    f"SL=OR-low({or_low:.2f}), T=2R({target:.2f})"
                ),
            )

        # ── SELL: broke below OR-low ───────────────────────────────────────────
        if entry < or_low:
            if vol_ratio < self.MIN_VOL_RATIO:
                return self._hold(entry, f"ORB breakdown without volume ({vol_ratio:.1f}x) — skip")

            drift_pct = (or_low - entry) / entry
            if drift_pct > 0.02:
                return self._hold(entry, f"Too far below OR-low ({drift_pct*100:.1f}%) — chasing short")

            confidence = 0.0
            if vol_ratio >= 2.0:
                confidence += 0.35
            elif vol_ratio >= 1.5:
                confidence += 0.25
            else:
                confidence += 0.15

            if drift_pct < 0.005:
                confidence += 0.25

            if vwap > 0 and entry < vwap:
                confidence += 0.20

            if prev_close and candles[0].get("o", or_low) < prev_close * 0.997:
                confidence += 0.15

            confidence = self._clamp(confidence)
            stop_loss = round(or_high, 2)
            target    = round(entry - 2.0 * or_range, 2)
            rr        = round((entry - target) / max(stop_loss - entry, 0.01), 2)

            return StrategySignal(
                action="SELL",
                confidence=confidence,
                entry=entry,
                stop_loss=stop_loss,
                target=target,
                risk_reward=rr,
                reasoning=(
                    f"ORB breakdown: {entry:.2f} < OR-low {or_low:.2f} "
                    f"(range={or_range:.2f}), vol={vol_ratio:.1f}x, "
                    f"SL=OR-high({or_high:.2f}), T=2R({target:.2f})"
                ),
            )

        return self._hold(
            entry,
            f"Price inside OR ({or_low:.2f}–{or_high:.2f}) — waiting for breakout"
        )
