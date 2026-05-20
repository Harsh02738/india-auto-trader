"""
TradingView Technical Analysis Collector.

Uses the tradingview-ta library to pull TradingView's computed indicator
summaries for NSE stocks. Symbols use the format "NSE:SYMBOL".

This is read-only market data — TradingView cannot execute trades for NSE.
For Pine Script strategy signals, use the /webhook/tradingview endpoint instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# TradingView recommendation values
TV_STRONG_BUY  = "STRONG_BUY"
TV_BUY         = "BUY"
TV_NEUTRAL     = "NEUTRAL"
TV_SELL        = "SELL"
TV_STRONG_SELL = "STRONG_SELL"

# Map TV recommendation → our action
_TV_TO_ACTION: dict[str, str] = {
    TV_STRONG_BUY:  "BUY",
    TV_BUY:         "BUY",
    TV_NEUTRAL:     "HOLD",
    TV_SELL:        "SELL",
    TV_STRONG_SELL: "SELL",
}


@dataclass
class TVTimeframeResult:
    interval: str                    # "5m", "15m", "1h", "4h", "1D"
    recommendation: str              # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    action: str                      # BUY / HOLD / SELL
    buy_count: int
    sell_count: int
    neutral_count: int
    rsi: float | None
    macd_signal: str | None          # "BUY" or "SELL"
    bb_position: str | None          # "above_upper" / "between" / "below_lower"
    ema_alignment: str | None        # "bullish" / "bearish" / "mixed"
    confidence: float                # 0-1 based on indicator agreement


@dataclass
class TVAnalysis:
    symbol: str
    exchange: str                    # "NSE"
    timeframes: dict[str, TVTimeframeResult]
    confluence_action: str           # majority action across timeframes
    confluence_score: float          # 0-1
    bullish_tf_count: int
    bearish_tf_count: int
    neutral_tf_count: int
    error: str | None = None


class TradingViewCollector:
    """
    Pulls TradingView technical analysis summaries for NSE stocks.

    Usage:
        collector = TradingViewCollector()
        analysis = collector.get_multi_timeframe_analysis("RELIANCE")
        # analysis.confluence_action → "BUY" / "HOLD" / "SELL"
    """

    # Intervals to poll for multi-timeframe analysis
    INTERVALS = {
        "5m":  "5m",
        "15m": "15m",
        "1h":  "1h",
        "4h":  "4h",
        "1D":  "1d",
    }

    def get_technical_summary(self, symbol: str, interval: str = "1D") -> TVTimeframeResult | None:
        """
        Get TradingView technical summary for a single timeframe.
        symbol: NSE symbol e.g. "RELIANCE" (exchange prefix added internally)
        interval: "5m", "15m", "1h", "4h", "1D"
        """
        try:
            from tradingview_ta import TA_Handler, Interval

            tv_interval = _map_interval(interval)
            handler = TA_Handler(
                symbol=symbol,
                screener="india",
                exchange="NSE",
                interval=tv_interval,
            )
            analysis = handler.get_analysis()
            return _parse_analysis(symbol, interval, analysis)

        except Exception as exc:
            logger.warning("[TV] %s %s error: %s", symbol, interval, exc)
            return None

    def get_multi_timeframe_analysis(self, symbol: str) -> TVAnalysis:
        """
        Poll 5m, 15m, 1h, 4h, 1D timeframes and compute confluence.
        Confluence = majority direction weighted by higher timeframes.
        """
        results: dict[str, TVTimeframeResult] = {}

        for label, _ in self.INTERVALS.items():
            tf_result = self.get_technical_summary(symbol, label)
            if tf_result is not None:
                results[label] = tf_result

        if not results:
            return TVAnalysis(
                symbol=symbol,
                exchange="NSE",
                timeframes={},
                confluence_action="HOLD",
                confluence_score=0.0,
                bullish_tf_count=0,
                bearish_tf_count=0,
                neutral_tf_count=0,
                error="No timeframe data available — check internet connection or symbol",
            )

        bullish = [r for r in results.values() if r.action == "BUY"]
        bearish = [r for r in results.values() if r.action == "SELL"]
        neutral = [r for r in results.values() if r.action == "HOLD"]

        # Higher timeframes (1h, 4h, 1D) carry double weight
        weights = {"5m": 1, "15m": 1, "1h": 2, "4h": 2, "1D": 3}
        bull_score = sum(weights.get(tf, 1) for tf, r in results.items() if r.action == "BUY")
        bear_score = sum(weights.get(tf, 1) for tf, r in results.items() if r.action == "SELL")
        total_weight = sum(weights.get(tf, 1) for tf in results)

        if bull_score > bear_score:
            confluence_action = "BUY"
            confluence_score = bull_score / total_weight
        elif bear_score > bull_score:
            confluence_action = "SELL"
            confluence_score = bear_score / total_weight
        else:
            confluence_action = "HOLD"
            confluence_score = 0.5

        return TVAnalysis(
            symbol=symbol,
            exchange="NSE",
            timeframes=results,
            confluence_action=confluence_action,
            confluence_score=round(confluence_score, 3),
            bullish_tf_count=len(bullish),
            bearish_tf_count=len(bearish),
            neutral_tf_count=len(neutral),
        )

    def scan_watchlist_tv(self, symbols: list[str]) -> list[dict]:
        """
        Batch TradingView analysis for a list of NSE symbols.
        Returns sorted list with BUY candidates first.
        """
        results = []
        for sym in symbols:
            analysis = self.get_multi_timeframe_analysis(sym)
            results.append({
                "symbol": sym,
                "action": analysis.confluence_action,
                "confluence_score": analysis.confluence_score,
                "bullish_tfs": analysis.bullish_tf_count,
                "bearish_tfs": analysis.bearish_tf_count,
                "error": analysis.error,
            })

        # Sort: BUY first (by score desc), then HOLD, then SELL
        action_order = {"BUY": 0, "HOLD": 1, "SELL": 2}
        results.sort(key=lambda x: (action_order.get(x["action"], 1), -x["confluence_score"]))
        return results

    def to_strategy_signal(self, analysis: TVAnalysis) -> dict | None:
        """
        Convert TVAnalysis into a StrategySignal-compatible dict for the consensus engine.
        Returns None if confluence is below threshold (< 0.55).
        """
        if analysis.confluence_score < 0.55 or analysis.confluence_action == "HOLD":
            return None

        return {
            "action": analysis.confluence_action,
            "confidence": analysis.confluence_score,
            "reasoning": (
                f"TradingView {analysis.bullish_tf_count}↑ / "
                f"{analysis.bearish_tf_count}↓ across 5 timeframes "
                f"(confluence {analysis.confluence_score:.0%})"
            ),
            "source": "TradingView",
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _map_interval(label: str):
    from tradingview_ta import Interval
    mapping = {
        "5m":  Interval.INTERVAL_5_MINUTES,
        "15m": Interval.INTERVAL_15_MINUTES,
        "1h":  Interval.INTERVAL_1_HOUR,
        "4h":  Interval.INTERVAL_4_HOURS,
        "1D":  Interval.INTERVAL_1_DAY,
    }
    return mapping.get(label, Interval.INTERVAL_1_DAY)


def _parse_analysis(symbol: str, interval: str, analysis) -> TVTimeframeResult:
    summary = analysis.summary
    rec = summary.get("RECOMMENDATION", TV_NEUTRAL)
    buy_c = summary.get("BUY", 0)
    sell_c = summary.get("SELL", 0)
    neut_c = summary.get("NEUTRAL", 0)
    total_ind = max(buy_c + sell_c + neut_c, 1)

    indicators = analysis.indicators or {}

    rsi = indicators.get("RSI")
    macd_hist = indicators.get("MACD.macd")
    macd_sig = indicators.get("MACD.signal")
    macd_signal = None
    if macd_hist is not None and macd_sig is not None:
        macd_signal = "BUY" if float(macd_hist) > float(macd_sig) else "SELL"

    # Bollinger Band position
    bb_lower = indicators.get("BB.lower")
    bb_upper = indicators.get("BB.upper")
    close = indicators.get("close")
    bb_position = None
    if bb_lower and bb_upper and close:
        c, lo, hi = float(close), float(bb_lower), float(bb_upper)
        if c > hi:
            bb_position = "above_upper"
        elif c < lo:
            bb_position = "below_lower"
        else:
            bb_position = "between"

    # EMA alignment (20/50/200)
    ema20 = indicators.get("EMA20")
    ema50 = indicators.get("EMA50")
    ema200 = indicators.get("EMA200")
    ema_alignment = None
    if close and ema20 and ema50 and ema200:
        c = float(close)
        if c > float(ema20) > float(ema50) > float(ema200):
            ema_alignment = "bullish"
        elif c < float(ema20) < float(ema50) < float(ema200):
            ema_alignment = "bearish"
        else:
            ema_alignment = "mixed"

    # Confidence = how one-sided the indicators are
    dominant = max(buy_c, sell_c)
    confidence = round(dominant / total_ind, 3)

    return TVTimeframeResult(
        interval=interval,
        recommendation=rec,
        action=_TV_TO_ACTION.get(rec, "HOLD"),
        buy_count=buy_c,
        sell_count=sell_c,
        neutral_count=neut_c,
        rsi=float(rsi) if rsi is not None else None,
        macd_signal=macd_signal,
        bb_position=bb_position,
        ema_alignment=ema_alignment,
        confidence=confidence,
    )
