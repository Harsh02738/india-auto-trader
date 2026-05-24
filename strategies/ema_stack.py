"""
9/21 EMA Stack — Short-term momentum confirmation on 5-min bars.

Edge: When the 9-EMA (fast trend) sits above the 21-EMA (slow trend) on
      5-minute charts, institutional momentum is aligned short-term. Used
      widely by Indian intraday traders as a trend filter. Confirmed by MACD
      histogram direction (momentum) and volume, with RSI guard for entries.

Signal: BUY when 9-EMA > 21-EMA AND price > 9-EMA AND MACD hist > 0.
        SELL on inverted stack with opposite conditions.
        No entries after 14:00 IST. SL = 1.5×ATR.
"""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

from .base import BaseStrategy, StrategySignal

IST = ZoneInfo("Asia/Kolkata")

_FAST = 9
_SLOW = 21
_CUTOFF_HOUR = 14


def _ema_from_candles(closes: list[float], period: int) -> float | None:
    """Compute final EMA value from a list of closes."""
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    val = sum(closes[:period]) / period
    for c in closes[period:]:
        val = c * k + val * (1 - k)
    return val


class EMAStackStrategy(BaseStrategy):
    name = "EMAStack"
    timeframe = "intraday"

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        now_ist = datetime.now(tz=IST)
        if now_ist.hour >= _CUTOFF_HOUR:
            return self._hold(entry, "Past 14:00 IST — EMAStack entry window closed")

        atr       = ohlcv.get("atr") or entry * 0.015
        vol_ratio = ohlcv.get("vol_ratio") or 1.0
        rsi       = ohlcv.get("rsi") or 50.0
        macd_hist = ohlcv.get("macd_hist") or 0.0

        # Prefer pre-computed values from realtime collector
        ema9  = ohlcv.get("ema9")
        ema21 = ohlcv.get("ema21")

        if ema9 is None or ema21 is None:
            candles = ohlcv.get("candles", [])
            if len(candles) < _SLOW + 2:
                return self._hold(entry, f"Need ≥{_SLOW + 2} candles for EMA stack")
            closes = [c["c"] for c in candles]
            ema9  = _ema_from_candles(closes, _FAST)
            ema21 = _ema_from_candles(closes, _SLOW)
            if ema9 is None or ema21 is None:
                return self._hold(entry, "EMA computation failed — insufficient data")

        stack_gap_pct = (ema9 - ema21) / entry   # signed: positive = bullish

        # ── BUY: bullish stack ─────────────────────────────────────────────────
        if ema9 > ema21:
            if entry < ema9:
                return self._hold(entry, f"Bullish stack but price {entry:.2f} < EMA9 {ema9:.2f}")
            if macd_hist <= 0:
                return self._hold(entry, f"Bullish stack but MACD hist={macd_hist:.4f} ≤ 0")
            if rsi > 72:
                return self._hold(entry, f"RSI {rsi:.0f} overbought — skip EMAStack long")
            if rsi < 40:
                return self._hold(entry, f"RSI {rsi:.0f} too weak for EMAStack long")

            confidence = 0.0
            gap = abs(stack_gap_pct)
            if gap >= 0.005:
                confidence += 0.30
            elif gap >= 0.002:
                confidence += 0.20
            else:
                confidence += 0.12

            if vol_ratio >= 1.5:
                confidence += 0.20
            elif vol_ratio >= 1.2:
                confidence += 0.12
            else:
                confidence += 0.05

            hist_norm = abs(macd_hist) / atr if atr > 0 else 0
            if hist_norm >= 0.3:
                confidence += 0.20
            elif hist_norm >= 0.15:
                confidence += 0.12
            else:
                confidence += 0.06

            if 45 <= rsi <= 65:
                confidence += 0.15
            elif rsi < 45:
                confidence += 0.08

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
                    f"EMAStack: EMA9({ema9:.2f}) > EMA21({ema21:.2f}), "
                    f"price>{ema9:.2f}, MACD_hist={macd_hist:.4f}, "
                    f"RSI={rsi:.0f}, vol={vol_ratio:.1f}x"
                ),
            )

        # ── SELL: bearish stack ────────────────────────────────────────────────
        if entry > ema9:
            return self._hold(entry, f"Bearish stack but price {entry:.2f} > EMA9 {ema9:.2f}")
        if macd_hist >= 0:
            return self._hold(entry, f"Bearish stack but MACD hist={macd_hist:.4f} ≥ 0")
        if rsi < 28:
            return self._hold(entry, f"RSI {rsi:.0f} oversold — skip EMAStack short")
        if rsi > 60:
            return self._hold(entry, f"RSI {rsi:.0f} too high for EMAStack short")

        confidence = 0.0
        gap = abs(stack_gap_pct)
        if gap >= 0.005:
            confidence += 0.30
        elif gap >= 0.002:
            confidence += 0.20
        else:
            confidence += 0.12

        if vol_ratio >= 1.5:
            confidence += 0.20
        elif vol_ratio >= 1.2:
            confidence += 0.12
        else:
            confidence += 0.05

        hist_norm = abs(macd_hist) / atr if atr > 0 else 0
        if hist_norm >= 0.3:
            confidence += 0.20
        elif hist_norm >= 0.15:
            confidence += 0.12
        else:
            confidence += 0.06

        if 35 <= rsi <= 55:
            confidence += 0.15
        elif rsi > 55:
            confidence += 0.08

        confidence = self._clamp(confidence)
        stop_loss = round(entry + 1.5 * atr, 2)
        target    = round(entry - 2.5 * atr, 2)
        rr        = round((entry - target) / max(stop_loss - entry, 0.01), 2)

        return StrategySignal(
            action="SELL",
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=rr,
            reasoning=(
                f"EMAStack: EMA9({ema9:.2f}) < EMA21({ema21:.2f}), "
                f"price<{ema9:.2f}, MACD_hist={macd_hist:.4f}, "
                f"RSI={rsi:.0f}, vol={vol_ratio:.1f}x"
            ),
        )
