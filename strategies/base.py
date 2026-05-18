"""
Base class and data types for all trading strategies.

Each strategy takes the OHLCV dict (from data_collector/market_data.py)
and optional fundamentals dict, and returns a StrategySignal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


Action = Literal["BUY", "SELL", "HOLD"]


@dataclass
class StrategySignal:
    action: Action
    confidence: float          # 0.0 – 1.0
    entry: float               # suggested entry price
    stop_loss: float           # suggested stop-loss price
    target: float              # suggested profit target
    risk_reward: float         # (target - entry) / (entry - stop_loss)
    reasoning: str             # one-line human-readable explanation


class BaseStrategy(ABC):
    name: str = "base"
    timeframe: Literal["intraday", "swing"] = "intraday"

    @abstractmethod
    def generate_signal(self, ohlcv: dict, fundamentals: dict | None = None) -> StrategySignal:
        """
        Analyse ohlcv data and return a StrategySignal.
        ohlcv: the dict written by data_collector/market_data.py collect_ohlcv()
        fundamentals: optional dict from data_collector/fundamental.py
        """

    # ── Shared helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _hold(entry: float, reason: str) -> StrategySignal:
        return StrategySignal(
            action="HOLD",
            confidence=0.0,
            entry=entry,
            stop_loss=entry,
            target=entry,
            risk_reward=0.0,
            reasoning=reason,
        )

    @staticmethod
    def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, val))

    @staticmethod
    def _adx(candles: list[dict], period: int = 14) -> float:
        """Compute ADX from last N+period candles. Returns 0 if insufficient data."""
        import math

        n = len(candles)
        needed = period * 2
        if n < needed:
            return 0.0

        highs  = [c["h"] for c in candles]
        lows   = [c["l"] for c in candles]
        closes = [c["c"] for c in candles]

        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, n):
            up   = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if (up > down and up > 0) else 0)
            minus_dm.append(down if (down > up and down > 0) else 0)
            h, l, pc = highs[i], lows[i], closes[i - 1]
            tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))

        def smooth(series, p):
            result = []
            s = sum(series[:p])
            result.append(s)
            for v in series[p:]:
                s = s - s / p + v
                result.append(s)
            return result

        atr_s   = smooth(tr_list, period)
        plus_s  = smooth(plus_dm, period)
        minus_s = smooth(minus_dm, period)

        dx_list = []
        for a, p, m in zip(atr_s, plus_s, minus_s):
            if a == 0:
                continue
            plus_di  = 100 * p / a
            minus_di = 100 * m / a
            denom = plus_di + minus_di
            if denom == 0:
                continue
            dx_list.append(100 * abs(plus_di - minus_di) / denom)

        if not dx_list:
            return 0.0
        return sum(dx_list[-period:]) / min(len(dx_list), period)

    @staticmethod
    def _vwap(candles: list[dict]) -> float:
        """Typical-price VWAP for the candles provided."""
        num = sum(((c["h"] + c["l"] + c["c"]) / 3) * c["v"] for c in candles)
        den = sum(c["v"] for c in candles)
        return num / den if den > 0 else 0.0

    @staticmethod
    def _roc(candles: list[dict], period: int) -> float:
        """Rate of Change over N periods as a decimal (0.10 = +10%)."""
        if len(candles) < period + 1:
            return 0.0
        new = candles[-1]["c"]
        old = candles[-(period + 1)]["c"]
        return (new - old) / old if old > 0 else 0.0

    @staticmethod
    def _bb_width(ohlcv: dict) -> float:
        """Bollinger Band width normalised by midband."""
        upper = ohlcv.get("bb_upper") or 0
        lower = ohlcv.get("bb_lower") or 0
        mid   = ohlcv.get("bb_mid") or 1
        return (upper - lower) / mid if mid > 0 else 0
