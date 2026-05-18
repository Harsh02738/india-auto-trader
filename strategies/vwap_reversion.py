"""
VWAP Mean Reversion Strategy (intraday).

Edge: Large institutional players use VWAP as a benchmark for execution.
      When price deviates significantly below VWAP in an uptrending stock,
      institutions buying near VWAP create a gravitational pull back up.
      This provides a high-probability intraday entry in the direction of
      the daily trend.

Signal: BUY when price is 1.5%+ below VWAP AND daily trend is up (EMA-200)
        AND RSI is not already oversold market-wide.
"""

from __future__ import annotations

from .base import BaseStrategy, StrategySignal


class VwapReversionStrategy(BaseStrategy):
    name = "VWAP"
    timeframe = "intraday"

    VWAP_DEVIATION_PCT = 0.015   # price must be 1.5%+ below VWAP
    VWAP_EXTREME_PCT   = 0.035   # above 3.5% deviation = too risky / news-driven

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        candles = ohlcv.get("candles", [])
        if not candles:
            return self._hold(entry, "No candle data for VWAP calculation")

        atr          = ohlcv.get("atr") or entry * 0.02
        above_ema200 = ohlcv.get("above_ema200", False)
        rsi          = ohlcv.get("rsi") or 50.0
        vol_ratio    = ohlcv.get("vol_ratio") or 1.0

        # Only go long in structural uptrend
        if not above_ema200:
            return self._hold(entry, "Below EMA-200: VWAP reversion long not valid")

        vwap = self._vwap(candles[-78:])  # ~today's intraday (78 × 5-min = 6.5h)
        if vwap <= 0:
            return self._hold(entry, "Could not compute VWAP")

        deviation = (vwap - entry) / vwap  # positive = price below VWAP

        if deviation < self.VWAP_DEVIATION_PCT:
            return self._hold(
                entry,
                f"Price only {deviation*100:.2f}% below VWAP (need >{self.VWAP_DEVIATION_PCT*100:.1f}%)",
            )

        if deviation > self.VWAP_EXTREME_PCT:
            return self._hold(
                entry,
                f"Deviation {deviation*100:.2f}% too large — likely news-driven gap, skip",
            )

        # RSI guard: if RSI < 25, might be a breakdown not a bounce
        if rsi < 25:
            return self._hold(entry, f"RSI {rsi:.0f} extremely oversold — possible breakdown, not bounce")

        confidence = 0.0

        # Deviation score
        if deviation >= 0.025:
            confidence += 0.35
        elif deviation >= 0.020:
            confidence += 0.28
        else:
            confidence += 0.20

        # Volume should confirm (volume present = real selling, not thin market)
        if vol_ratio >= 1.5:
            confidence += 0.15
        elif vol_ratio >= 1.0:
            confidence += 0.08

        # RSI in mild oversold = better reversion probability
        if 30 <= rsi <= 42:
            confidence += 0.15
        elif rsi < 30:
            confidence += 0.10
        else:
            confidence += 0.05

        if above_ema200:
            confidence += 0.12  # structural trend confirmation

        confidence = self._clamp(confidence)

        stop_loss = round(entry - 1.5 * atr, 2)
        target    = round(min(vwap + 0.002 * vwap, entry + 2.0 * atr), 2)
        rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

        return StrategySignal(
            action="BUY",
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=rr,
            reasoning=(
                f"VWAP: {deviation*100:.1f}% below VWAP={vwap:.2f}, "
                f"RSI={rsi:.0f}, vol_ratio={vol_ratio:.1f}x, target=VWAP"
            ),
        )
