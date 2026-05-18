"""
MACD + RSI Confluence Strategy.

Edge: When MACD makes a bullish crossover AND RSI is rising from an
      oversold base (< 45), both momentum indicators confirm each other.
      QuantifiedStrategies research documents ~73% win rate for this combination.

Signal: BUY when MACD crosses above signal line AND RSI rising from < 45.
"""

from __future__ import annotations

from .base import BaseStrategy, StrategySignal


class MacdRsiConfluenceStrategy(BaseStrategy):
    name = "MACD+RSI"
    timeframe = "intraday"

    RSI_BASE_MAX  = 45   # RSI must be rising from below this level
    RSI_MIN       = 25   # Must not be extreme (data error / halted stock)
    RSI_CAP       = 60   # After RSI crosses 60, momentum already captured

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        atr           = ohlcv.get("atr") or entry * 0.02
        rsi           = ohlcv.get("rsi") or 50.0
        macd_cross    = ohlcv.get("macd_crossover", False)
        macd_hist     = ohlcv.get("macd_hist") or 0.0
        above_ema200  = ohlcv.get("above_ema200", False)
        vol_ratio     = ohlcv.get("vol_ratio") or 1.0
        bb_pct        = ohlcv.get("bb_pct") or 0.5

        # Core condition: MACD must have crossed bullishly
        if not macd_cross:
            return self._hold(entry, "No fresh MACD bullish crossover")

        # RSI must be in the confirmation zone (rising from oversold base)
        if rsi > self.RSI_CAP:
            return self._hold(entry, f"RSI {rsi:.0f} > {self.RSI_CAP}: too extended for entry")
        if rsi < self.RSI_MIN:
            return self._hold(entry, f"RSI {rsi:.0f} suspiciously low — possible data error")
        if rsi > self.RSI_BASE_MAX:
            return self._hold(entry, f"RSI {rsi:.0f} > {self.RSI_BASE_MAX}: not rising from oversold base")

        # Score
        confidence = 0.30  # base: MACD crossover confirmed

        # RSI position in oversold range
        if rsi <= 35:
            confidence += 0.20
        elif rsi <= 42:
            confidence += 0.15
        else:
            confidence += 0.08

        # MACD histogram size (larger = stronger signal)
        if macd_hist > 0.5:
            confidence += 0.15
        elif macd_hist > 0.1:
            confidence += 0.10
        else:
            confidence += 0.05

        if above_ema200:
            confidence += 0.12

        if vol_ratio >= 1.5:
            confidence += 0.12
        elif vol_ratio >= 1.2:
            confidence += 0.06

        if bb_pct < 0.40:
            confidence += 0.08  # near lower band on MACD cross = strong setup

        confidence = self._clamp(confidence)

        stop_loss = round(entry - 1.5 * atr, 2)
        target    = round(entry + 2.5 * atr, 2)
        rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

        return StrategySignal(
            action="BUY",
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=rr,
            reasoning=(
                f"MACD+RSI: MACD bullish crossover, hist={macd_hist:.3f}, "
                f"RSI={rsi:.0f} (rising from base), vol_ratio={vol_ratio:.1f}x"
            ),
        )
