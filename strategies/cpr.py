"""
Central Pivot Range (CPR) — Highly popular among Indian intraday traders.

Edge: CPR levels computed from the previous day's H/L/C act as dynamic
      support/resistance for the next session. Price above TC (Top Central)
      = bullish bias; below BC (Bottom Central) = bearish. A Virgin CPR
      (price hasn't touched either level yet today) is the strongest signal.

Calculation:
    Pivot (P) = (prev_H + prev_L + prev_C) / 3
    BC        = (prev_H + prev_L) / 2
    TC        = (P - BC) + P
    Width = TC - BC (narrow = range day; wide = trending day)

Signal: BUY when 5-min candle closes above TC. SELL below BC.
        Skip if CPR width < 0.1% of price (narrow = range day expected).
"""

from __future__ import annotations

from .base import BaseStrategy, StrategySignal

_NARROW_CPR_PCT  = 0.001   # skip if CPR width < 0.1% of price
_VIRGIN_BONUS    = 0.15    # confidence bonus for untouched (virgin) CPR
_CHASE_LIMIT_PCT = 0.03    # skip if price is >3% beyond TC/BC


class CPRStrategy(BaseStrategy):
    name = "CPR"
    timeframe = "intraday"

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        atr       = ohlcv.get("atr") or entry * 0.015
        vol_ratio = ohlcv.get("vol_ratio") or 1.0
        rsi       = ohlcv.get("rsi") or 50.0

        # Previous day H/L/C — injected by kotak_realtime.py or daily cache
        prev_high  = ohlcv.get("prev_day_high")
        prev_low   = ohlcv.get("prev_day_low")
        prev_close = ohlcv.get("prev_day_close")

        if not all([prev_high, prev_low, prev_close]):
            # Fallback: infer from daily candle series (second-to-last bar)
            candles = ohlcv.get("candles", [])
            if len(candles) < 2:
                return self._hold(entry, "No prev-day H/L/C for CPR — need daily candles")
            prev = candles[-2]
            prev_high  = prev["h"]
            prev_low   = prev["l"]
            prev_close = prev["c"]

        # CPR levels
        pivot  = (prev_high + prev_low + prev_close) / 3.0
        bc     = (prev_high + prev_low) / 2.0
        tc     = (pivot - bc) + pivot
        if tc < bc:
            tc, bc = bc, tc   # edge case: strong bear day inverts levels

        cpr_width     = tc - bc
        cpr_width_pct = cpr_width / entry

        if cpr_width_pct < _NARROW_CPR_PCT:
            return self._hold(
                entry,
                f"Narrow CPR ({cpr_width_pct*100:.2f}%) — range day expected, skip"
            )

        # Virgin CPR detection: price hasn't touched TC or BC during today's session
        candles = ohlcv.get("candles", [])
        is_virgin = False
        if candles:
            session = candles[-78:]  # up to 78 five-min bars in a 6.5h session
            touched = any(c["h"] >= tc or c["l"] <= bc for c in session)
            is_virgin = not touched

        virgin_bonus = _VIRGIN_BONUS if is_virgin else 0.0

        # ── BUY: price above TC ────────────────────────────────────────────────
        if entry > tc:
            dist_pct = (entry - tc) / entry
            if dist_pct > _CHASE_LIMIT_PCT:
                return self._hold(entry, f"{dist_pct*100:.1f}% above TC — chasing CPR long")

            confidence = 0.0
            if dist_pct < 0.005:
                confidence += 0.35   # fresh breakout above TC
            elif dist_pct < 0.015:
                confidence += 0.22

            if vol_ratio >= 1.5:
                confidence += 0.20
            elif vol_ratio >= 1.2:
                confidence += 0.12

            if cpr_width_pct >= 0.005:
                confidence += 0.15   # wide CPR = strong level
            elif cpr_width_pct >= 0.003:
                confidence += 0.08

            confidence += virgin_bonus

            if rsi < 60:
                confidence += 0.10
            elif rsi > 75:
                confidence -= 0.10

            confidence = self._clamp(confidence)
            stop_loss = round(bc - 0.5 * atr, 2)
            target    = round(entry + 2.5 * cpr_width, 2)
            rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

            return StrategySignal(
                action="BUY",
                confidence=confidence,
                entry=entry,
                stop_loss=stop_loss,
                target=target,
                risk_reward=rr,
                reasoning=(
                    f"CPR: {entry:.2f} > TC={tc:.2f} | BC={bc:.2f} P={pivot:.2f} "
                    f"width={cpr_width_pct*100:.2f}%"
                    + (" [VIRGIN]" if is_virgin else "")
                ),
            )

        # ── SELL: price below BC ───────────────────────────────────────────────
        if entry < bc:
            dist_pct = (bc - entry) / entry
            if dist_pct > _CHASE_LIMIT_PCT:
                return self._hold(entry, f"{dist_pct*100:.1f}% below BC — chasing CPR short")

            confidence = 0.0
            if dist_pct < 0.005:
                confidence += 0.35
            elif dist_pct < 0.015:
                confidence += 0.22

            if vol_ratio >= 1.5:
                confidence += 0.20
            elif vol_ratio >= 1.2:
                confidence += 0.12

            if cpr_width_pct >= 0.005:
                confidence += 0.15
            elif cpr_width_pct >= 0.003:
                confidence += 0.08

            confidence += virgin_bonus

            if rsi > 40:
                confidence += 0.10
            elif rsi < 25:
                confidence -= 0.10

            confidence = self._clamp(confidence)
            stop_loss = round(tc + 0.5 * atr, 2)
            target    = round(entry - 2.5 * cpr_width, 2)
            rr        = round((entry - target) / max(stop_loss - entry, 0.01), 2)

            return StrategySignal(
                action="SELL",
                confidence=confidence,
                entry=entry,
                stop_loss=stop_loss,
                target=target,
                risk_reward=rr,
                reasoning=(
                    f"CPR: {entry:.2f} < BC={bc:.2f} | TC={tc:.2f} P={pivot:.2f} "
                    f"width={cpr_width_pct*100:.2f}%"
                    + (" [VIRGIN]" if is_virgin else "")
                ),
            )

        return self._hold(
            entry,
            f"Price {entry:.2f} inside CPR ({bc:.2f}–{tc:.2f}) — wait for breakout"
        )
