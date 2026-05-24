"""
Gap and Go — Intraday momentum strategy for NSE.

Edge: Stocks that gap up/down significantly at open have institutional or
      news-driven conviction. If the first 15-min candle (9:15–9:30 AM)
      confirms the gap direction with a strong body and volume surge,
      momentum tends to continue through 10:30–11:00 AM.

Signal: Gap ≥0.5% AND first 15-min candle confirms direction → enter on
        breakout above/below the 15-min candle. Never fade the gap.
        Valid only until 11:00 IST (gap momentum fades after that).
        SL = opposite end of the first 15-min candle.
"""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

from .base import BaseStrategy, StrategySignal

IST = ZoneInfo("Asia/Kolkata")

_MIN_GAP_PCT   = 0.005   # 0.5% minimum gap
_MAX_GAP_PCT   = 0.08    # 8% max (circuit-limit territory → skip)
_ENTRY_CUTOFF  = 11      # only trade until 11:00 IST
_FIRST_BARS    = 3       # 3 × 5-min = 15-min first candle


class GapAndGoStrategy(BaseStrategy):
    name = "GapAndGo"
    timeframe = "intraday"

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        now_ist = datetime.now(tz=IST)
        if now_ist.hour >= _ENTRY_CUTOFF:
            return self._hold(entry, "Gap and Go valid only until 11:00 IST")

        candles = ohlcv.get("candles", [])
        if len(candles) < _FIRST_BARS:
            return self._hold(entry, "Need first 15-min candles for Gap and Go")

        vol_ratio  = ohlcv.get("vol_ratio") or 1.0
        atr        = ohlcv.get("atr") or entry * 0.015
        session_open = ohlcv.get("session_open") or candles[0].get("o", entry)
        prev_close   = ohlcv.get("prev_day_close")

        if not prev_close or prev_close <= 0:
            return self._hold(entry, "No prev_day_close — cannot compute gap")

        gap_pct = (session_open - prev_close) / prev_close

        if abs(gap_pct) < _MIN_GAP_PCT:
            return self._hold(entry, f"Gap {gap_pct*100:.2f}% < {_MIN_GAP_PCT*100:.1f}% threshold")

        if abs(gap_pct) > _MAX_GAP_PCT:
            return self._hold(entry, f"Gap {gap_pct*100:.1f}% extreme — likely circuit event, skip")

        first_bars   = candles[:_FIRST_BARS]
        first_high   = max(c["h"] for c in first_bars)
        first_low    = min(c["l"] for c in first_bars)
        first_open_p = first_bars[0].get("o", session_open)
        first_close_p = first_bars[-1]["c"]
        first_range  = first_high - first_low
        first_body   = abs(first_close_p - first_open_p)
        body_ratio   = (first_body / first_range) if first_range > 0 else 0

        # ── BUY: gap-up confirmed ──────────────────────────────────────────────
        if gap_pct >= _MIN_GAP_PCT:
            if first_close_p <= first_open_p:
                return self._hold(entry, f"Gap-up but first candle is bearish — gap filling, skip")

            if entry < first_high:
                return self._hold(entry, f"Waiting for price to break first-bar high {first_high:.2f}")

            if entry > first_high * 1.015:
                return self._hold(entry, "Too far above first 15-min high — chasing")

            if vol_ratio < 1.0:
                return self._hold(entry, "Gap-up without volume surge — weak")

            confidence = 0.0
            if gap_pct >= 0.02:
                confidence += 0.35
            elif gap_pct >= 0.01:
                confidence += 0.25
            else:
                confidence += 0.15

            if vol_ratio >= 2.5:
                confidence += 0.30
            elif vol_ratio >= 1.5:
                confidence += 0.20
            else:
                confidence += 0.10

            if body_ratio >= 0.70:
                confidence += 0.20
            elif body_ratio >= 0.50:
                confidence += 0.12

            confidence = self._clamp(confidence)
            stop_loss = round(first_low, 2)
            target    = round(entry + 2.0 * first_range, 2)
            rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

            return StrategySignal(
                action="BUY",
                confidence=confidence,
                entry=entry,
                stop_loss=stop_loss,
                target=target,
                risk_reward=rr,
                reasoning=(
                    f"GapAndGo: +{gap_pct*100:.1f}% gap-up, bullish 15-min candle "
                    f"(body={body_ratio*100:.0f}%), vol={vol_ratio:.1f}x, "
                    f"SL=15min-low({stop_loss:.2f})"
                ),
            )

        # ── SELL: gap-down confirmed ───────────────────────────────────────────
        if first_close_p >= first_open_p:
            return self._hold(entry, f"Gap-down but first candle is bullish — gap filling, skip")

        if entry > first_low:
            return self._hold(entry, f"Waiting for price to break first-bar low {first_low:.2f}")

        if entry < first_low * 0.985:
            return self._hold(entry, "Too far below first 15-min low — chasing short")

        if vol_ratio < 1.0:
            return self._hold(entry, "Gap-down without volume — weak")

        confidence = 0.0
        if abs(gap_pct) >= 0.02:
            confidence += 0.35
        elif abs(gap_pct) >= 0.01:
            confidence += 0.25
        else:
            confidence += 0.15

        if vol_ratio >= 2.5:
            confidence += 0.30
        elif vol_ratio >= 1.5:
            confidence += 0.20
        else:
            confidence += 0.10

        if body_ratio >= 0.70:
            confidence += 0.20
        elif body_ratio >= 0.50:
            confidence += 0.12

        confidence = self._clamp(confidence)
        stop_loss = round(first_high, 2)
        target    = round(entry - 2.0 * first_range, 2)
        rr        = round((entry - target) / max(stop_loss - entry, 0.01), 2)

        return StrategySignal(
            action="SELL",
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=rr,
            reasoning=(
                f"GapAndGo: {gap_pct*100:.1f}% gap-down, bearish 15-min candle "
                f"(body={body_ratio*100:.0f}%), vol={vol_ratio:.1f}x, "
                f"SL=15min-high({stop_loss:.2f})"
            ),
        )
