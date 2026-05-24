"""
Supertrend Strategy — widely used in Indian markets.

Edge: Supertrend uses ATR to dynamically place a trailing stop that flips
      direction when price crosses it. When it flips to bullish (green),
      institutional and retail traders alike act on the signal, creating
      a self-reinforcing momentum effect. Very popular on NSE.

Signal: BUY when Supertrend flips from bearish to bullish (price crosses
        above the Supertrend line). Confirmed by price above Bollinger midband.
"""

from __future__ import annotations

from .base import BaseStrategy, StrategySignal


class SupertrendStrategy(BaseStrategy):
    name = "Supertrend"
    timeframe = "intraday"

    ATR_MULT = 3.0   # standard Supertrend multiplier
    PERIOD   = 10    # ATR period for Supertrend

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        candles = ohlcv.get("candles", [])
        atr     = ohlcv.get("atr") or entry * 0.02

        # Need at least PERIOD+2 candles to compute Supertrend flip
        if len(candles) < self.PERIOD + 2:
            return self._hold(entry, "Insufficient candles for Supertrend calculation")

        trend, prev_trend = self._compute_supertrend(candles)

        # Bullish flip = bearish → bullish
        bullish_flip = (trend == 1 and prev_trend == -1)
        # Already bullish (sustained)
        sustained_bullish = (trend == 1 and prev_trend == 1)

        if trend != 1:
            return self._hold(entry, "Supertrend is bearish (red) — no long entry")

        above_bb_mid = entry > (ohlcv.get("bb_mid") or entry)
        vol_ratio    = ohlcv.get("vol_ratio") or 1.0
        rsi          = ohlcv.get("rsi") or 50.0
        above_ema200 = ohlcv.get("above_ema200", False)

        confidence = 0.0

        if bullish_flip:
            confidence += 0.40  # fresh flip = strongest signal
        elif sustained_bullish:
            confidence += 0.20  # still bullish but not a new flip

        if above_bb_mid:
            confidence += 0.15

        if vol_ratio >= 1.5:
            confidence += 0.15
        elif vol_ratio >= 1.2:
            confidence += 0.08

        if above_ema200:
            confidence += 0.12

        if rsi < 65:
            confidence += 0.10
        elif rsi < 55:
            confidence += 0.15

        confidence = self._clamp(confidence)

        stop_loss = round(entry - self.ATR_MULT * atr, 2)
        target    = round(entry + self.ATR_MULT * 2.5 * atr, 2)
        rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

        flip_desc = "bullish FLIP" if bullish_flip else "bullish (sustained)"
        return StrategySignal(
            action="BUY",
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=rr,
            reasoning=(
                f"Supertrend {flip_desc}, vol_ratio={vol_ratio:.1f}x, "
                f"RSI={rsi:.0f}, above_ema200={above_ema200}"
            ),
        )

    def _compute_supertrend(self, candles: list[dict]) -> tuple[int, int]:
        """
        Returns (current_trend, prev_trend) where 1=bullish, -1=bearish.
        Uses last PERIOD+2 candles for efficiency.
        """
        subset = candles[-(self.PERIOD + 2):]
        n = len(subset)

        highs  = [c["h"] for c in subset]
        lows   = [c["l"] for c in subset]
        closes = [c["c"] for c in subset]

        # True Range and smoothed ATR
        tr_list = []
        for i in range(1, n):
            h, l, pc = highs[i], lows[i], closes[i - 1]
            tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))

        if not tr_list:
            return 1, 1

        # Wilder smooth
        atr = tr_list[0]
        atr_series = [atr]
        for tr in tr_list[1:]:
            atr = (atr * (self.PERIOD - 1) + tr) / self.PERIOD
            atr_series.append(atr)

        # Basic upper / lower bands
        direction = []
        prev_up = prev_dn = 0.0
        for i, atr_val in enumerate(atr_series):
            idx = i + 1  # offset because TR starts from index 1
            hl2 = (highs[idx] + lows[idx]) / 2
            up  = hl2 - self.ATR_MULT * atr_val
            dn  = hl2 + self.ATR_MULT * atr_val

            # Clamp so bands don't widen against position
            up = max(up, prev_up) if closes[idx - 1] > prev_up else up
            dn = min(dn, prev_dn) if closes[idx - 1] < prev_dn else dn

            if closes[idx] > prev_dn:
                direction.append(1)   # bullish
            elif closes[idx] < prev_up:
                direction.append(-1)  # bearish
            else:
                direction.append(direction[-1] if direction else 1)

            prev_up, prev_dn = up, dn

        if len(direction) >= 2:
            return direction[-1], direction[-2]
        if direction:
            return direction[-1], direction[-1]
        return 1, 1
