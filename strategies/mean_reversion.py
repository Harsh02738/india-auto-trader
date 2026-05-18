"""
RSI Oversold Mean Reversion Strategy.

Edge: Short-term oversold pullbacks in stocks that are in a structural uptrend
      (price > EMA-200) have a documented tendency to bounce back toward the
      moving average. Win rate historically 60–68%.

Signal: BUY when RSI < 32 AND price above EMA-200 AND volume confirms.
"""

from __future__ import annotations

from .base import BaseStrategy, StrategySignal


class MeanReversionStrategy(BaseStrategy):
    name = "MeanReversion"
    timeframe = "intraday"

    RSI_STRONG_OVERSOLD  = 28
    RSI_OVERSOLD         = 32
    RSI_MILD_OVERSOLD    = 40
    BB_OVERSOLD_ZONE     = 0.20   # bb_pct below this = near lower band

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        atr          = ohlcv.get("atr") or entry * 0.02
        rsi          = ohlcv.get("rsi") or 50.0
        bb_pct       = ohlcv.get("bb_pct") or 0.5
        above_ema200 = ohlcv.get("above_ema200", False)
        vol_ratio    = ohlcv.get("vol_ratio") or 1.0
        ema20        = ohlcv.get("ema20") or entry
        macd_cross   = ohlcv.get("macd_crossover", False)

        # Must be in a structural uptrend
        if not above_ema200:
            return self._hold(entry, "Below EMA-200: mean reversion long not valid")

        # RSI must be in oversold territory
        if rsi >= self.RSI_MILD_OVERSOLD:
            return self._hold(entry, f"RSI {rsi:.0f} not oversold enough (need < {self.RSI_MILD_OVERSOLD})")

        # Score
        confidence = 0.0

        if rsi <= self.RSI_STRONG_OVERSOLD:
            confidence += 0.40
        elif rsi <= self.RSI_OVERSOLD:
            confidence += 0.30
        else:
            confidence += 0.15

        if bb_pct <= self.BB_OVERSOLD_ZONE:
            confidence += 0.20  # near lower Bollinger Band

        if vol_ratio >= 1.3:
            confidence += 0.15
        elif vol_ratio >= 1.0:
            confidence += 0.05

        if macd_cross:
            confidence += 0.15  # MACD confirming reversal

        confidence = self._clamp(confidence)

        # Target = EMA-20 (mean reversion target)
        target    = round(max(ema20, entry + 1.5 * atr), 2)
        stop_loss = round(entry - 1.5 * atr, 2)
        rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

        return StrategySignal(
            action="BUY",
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=rr,
            reasoning=(
                f"MeanReversion: RSI={rsi:.0f} oversold, BB%={bb_pct:.2f}, "
                f"vol_ratio={vol_ratio:.1f}x, above EMA-200, target EMA-20={ema20:.2f}"
            ),
        )
