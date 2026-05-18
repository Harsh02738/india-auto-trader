"""
Relative Momentum Strategy — inspired by Andreas Clenow's "Stocks on the Move".

Edge: Stocks with strong 20-day rate-of-change in a confirmed trend
      (ADX > 25) continue to outperform. Momentum is the most documented
      factor anomaly in academic literature.

Signal: BUY when ROC-20 is strongly positive AND ADX confirms trend strength.
"""

from __future__ import annotations

from .base import BaseStrategy, StrategySignal


class MomentumStrategy(BaseStrategy):
    name = "Momentum"
    timeframe = "swing"

    # Tune thresholds
    ROC_STRONG   = 0.08   # +8% in 20 days = strong momentum
    ROC_MODERATE = 0.04   # +4% = moderate
    ADX_TREND    = 25     # ADX > 25 = trend present
    ADX_STRONG   = 35     # ADX > 35 = strong trend

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        atr = ohlcv.get("atr") or entry * 0.02
        candles = ohlcv.get("candles", [])

        # Rate of change over 20 days (use available candles)
        roc = self._roc(candles, min(20, len(candles) - 1))

        # ADX from candles
        adx = self._adx(candles, period=14)

        above_ema200 = ohlcv.get("above_ema200", False)
        vol_ratio    = ohlcv.get("vol_ratio") or 1.0
        rsi          = ohlcv.get("rsi") or 50.0

        # Reject if in a downtrend
        if not above_ema200:
            return self._hold(entry, "Below EMA-200: no long momentum trades")

        # Reject if RSI overbought (chasing)
        if rsi > 75:
            return self._hold(entry, f"RSI {rsi:.0f} overbought — momentum already extended")

        # Require meaningful trend
        if adx < self.ADX_TREND:
            return self._hold(entry, f"ADX {adx:.1f} < {self.ADX_TREND}: trend too weak for momentum")

        # Score momentum strength
        confidence = 0.0
        if roc >= self.ROC_STRONG:
            confidence += 0.45
        elif roc >= self.ROC_MODERATE:
            confidence += 0.25
        else:
            return self._hold(entry, f"ROC {roc*100:.1f}% below {self.ROC_MODERATE*100:.0f}% threshold")

        if adx >= self.ADX_STRONG:
            confidence += 0.25
        elif adx >= self.ADX_TREND:
            confidence += 0.15

        if vol_ratio >= 1.5:
            confidence += 0.15
        elif vol_ratio >= 1.2:
            confidence += 0.08

        if rsi < 65:
            confidence += 0.10  # not yet overbought — room to run
        elif rsi < 70:
            confidence += 0.05

        confidence = self._clamp(confidence)

        stop_loss = round(entry - 2.0 * atr, 2)
        target    = round(entry + 4.0 * atr, 2)
        rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

        return StrategySignal(
            action="BUY",
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=rr,
            reasoning=(
                f"Momentum: ROC-20={roc*100:.1f}%, ADX={adx:.0f}, "
                f"vol_ratio={vol_ratio:.1f}x, RSI={rsi:.0f}, above EMA-200"
            ),
        )
