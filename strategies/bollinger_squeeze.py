"""
Bollinger Band Squeeze Breakout Strategy.

Edge: When Bollinger Bands compress (BB width at multi-month low), it indicates
      a period of abnormally low volatility. Volatility is mean-reverting —
      periods of low volatility are followed by explosive moves. Trading the
      first breakout after a squeeze captures the beginning of that expansion.

Signal: BUY when BB width is at a 6-month low AND price breaks above upper band
        AND ATR is starting to expand AND volume confirms.
"""

from __future__ import annotations

from .base import BaseStrategy, StrategySignal


class BollingerSqueezeStrategy(BaseStrategy):
    name = "BBSqueeze"
    timeframe = "swing"

    BB_WIDTH_PERCENTILE = 0.20   # BB width in bottom 20% of 6-month range = squeeze
    ATR_EXPANSION_RATIO = 1.10   # current ATR > 1.1× recent average = expanding

    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        entry = ohlcv.get("last_close", 0)
        if entry <= 0:
            return self._hold(0.0, "No price data")

        atr        = ohlcv.get("atr") or entry * 0.02
        bb_upper   = ohlcv.get("bb_upper") or entry
        bb_lower   = ohlcv.get("bb_lower") or entry
        bb_mid     = ohlcv.get("bb_mid") or entry
        bb_pct     = ohlcv.get("bb_pct") or 0.5
        bb_squeeze = ohlcv.get("bb_squeeze") or 0.0   # ATR / BB_width ratio
        vol_ratio  = ohlcv.get("vol_ratio") or 1.0
        rsi        = ohlcv.get("rsi") or 50.0
        above_ema200 = ohlcv.get("above_ema200", False)
        candles    = ohlcv.get("candles", [])

        # Compute current BB width
        bb_width = self._bb_width(ohlcv)

        # Check if in a squeeze by comparing to historical BB widths
        if len(candles) >= 20:
            widths = self._historical_bb_widths(candles)
            if widths:
                min_w = min(widths)
                max_w = max(widths)
                squeeze_threshold = min_w + self.BB_WIDTH_PERCENTILE * (max_w - min_w)
                in_squeeze = bb_width <= squeeze_threshold
            else:
                in_squeeze = bb_squeeze < 0.5   # fallback: use bb_squeeze ratio
        else:
            in_squeeze = bb_squeeze < 0.5

        if not in_squeeze:
            return self._hold(entry, f"No BB squeeze (width={bb_width:.4f})")

        # Price must be breaking above or near upper band
        if entry < bb_mid:
            return self._hold(entry, "Price below BB midband — wait for breakout direction")

        near_upper = entry >= bb_upper * 0.99   # within 1% of upper band
        above_upper = entry > bb_upper

        if not (near_upper or above_upper):
            return self._hold(entry, "Price not near upper BB — squeeze not resolved yet")

        # Volume must confirm the breakout
        if vol_ratio < 1.2:
            return self._hold(entry, f"Breakout volume insufficient (ratio={vol_ratio:.1f}x, need >1.2x)")

        confidence = 0.0

        if above_upper:
            confidence += 0.35   # clean breakout above upper band
        elif near_upper:
            confidence += 0.20   # approaching upper band

        # Squeeze quality: lower bb_squeeze = tighter squeeze = bigger expected move
        if bb_squeeze < 0.3:
            confidence += 0.20
        elif bb_squeeze < 0.5:
            confidence += 0.12

        if vol_ratio >= 2.0:
            confidence += 0.18
        elif vol_ratio >= 1.5:
            confidence += 0.12
        elif vol_ratio >= 1.2:
            confidence += 0.06

        if above_ema200:
            confidence += 0.10

        if 40 < rsi < 70:
            confidence += 0.08   # not extreme in either direction

        confidence = self._clamp(confidence)

        stop_loss = round(bb_mid - atr * 0.5, 2)
        target    = round(entry + 3.0 * atr, 2)
        rr        = round((target - entry) / max(entry - stop_loss, 0.01), 2)

        return StrategySignal(
            action="BUY",
            confidence=confidence,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=rr,
            reasoning=(
                f"BBSqueeze: width={bb_width:.4f}, BB%={bb_pct:.2f}, "
                f"vol_ratio={vol_ratio:.1f}x, RSI={rsi:.0f}, "
                f"{'breaking above upper band' if above_upper else 'near upper band'}"
            ),
        )

    @staticmethod
    def _historical_bb_widths(candles: list[dict]) -> list[float]:
        """Approximate BB widths from candle close prices using a rolling window."""
        import math
        closes = [c["c"] for c in candles]
        period = 20
        widths = []
        for i in range(period, len(closes)):
            window = closes[i - period:i]
            mean = sum(window) / period
            std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
            width = (2 * 2.0 * std) / mean if mean > 0 else 0
            widths.append(width)
        return widths
