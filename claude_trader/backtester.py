"""
Historical strategy backtester.

Downloads 5m OHLCV data from yfinance and runs the StrategyEngine
bar-by-bar (no look-ahead bias) to simulate paper trades.

Usage:
    from claude_trader.backtester import run_backtest
    result = run_backtest("RELIANCE", days=60)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Reuse indicator helpers from the realtime collector
try:
    from data_collector.kotak_realtime import (
        _compute_vwap,
        _compute_atr,
        _compute_rsi,
        _ema_update,
    )
except ImportError:
    # Fallback implementations if import fails
    def _compute_vwap(bars):
        num = sum(((b["h"] + b["l"] + b["c"]) / 3) * b.get("v", 1) for b in bars)
        den = sum(b.get("v", 1) for b in bars)
        return round(num / den, 2) if den > 0 else 0.0

    def _compute_atr(bars, period=14):
        if len(bars) < 2:
            return 0.0
        trs = [max(bars[i]["h"] - bars[i]["l"],
                   abs(bars[i]["h"] - bars[i-1]["c"]),
                   abs(bars[i]["l"] - bars[i-1]["c"])) for i in range(1, len(bars))]
        if not trs:
            return 0.0
        val = sum(trs[:period]) / min(len(trs), period)
        for tr in trs[period:]:
            k = 2.0 / (period + 1)
            val = tr * k + val * (1 - k)
        return round(val, 4)

    def _compute_rsi(bars, period=14):
        closes = [b["c"] for b in bars]
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(d, 0) for d in deltas]
        losses = [max(-d, 0) for d in deltas]
        avg_g  = sum(gains[:period]) / period
        avg_l  = sum(losses[:period]) / period
        for g, l in zip(gains[period:], losses[period:]):
            avg_g = (avg_g * (period - 1) + g) / period
            avg_l = (avg_l * (period - 1) + l) / period
        return round(100 - 100 / (1 + avg_g / max(avg_l, 1e-9)), 2)

    def _ema_update(prev, new, period):
        k = 2.0 / (period + 1)
        return new * k + prev * (1 - k)


@dataclass
class Trade:
    symbol: str
    action: str          # BUY | SELL
    entry: float
    entry_idx: int
    stop_loss: float
    target: float
    qty: int = 1
    exit_price: float = 0.0
    exit_idx: int = 0
    pnl: float = 0.0
    closed: bool = False
    exit_reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    signals_at: dict[int, object] = field(default_factory=dict)
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown: float = 0.0
    n_trades: int = 0
    chart_path: str = ""

    def summary(self) -> dict:
        return {
            "symbol":       self.symbol,
            "n_trades":     self.n_trades,
            "win_rate":     round(self.win_rate, 3),
            "total_pnl":    round(self.total_pnl, 2),
            "avg_win":      round(self.avg_win, 2),
            "avg_loss":     round(self.avg_loss, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "chart_path":   self.chart_path,
        }


def run_backtest(symbol: str, days: int = 60, min_votes: int = 3) -> BacktestResult:
    """
    Download yfinance 5m data and run StrategyEngine bar-by-bar.
    Returns BacktestResult with trades + chart path.
    """
    bars = _fetch_bars(symbol, days)
    if len(bars) < 60:
        logger.warning("[Backtest] %s: only %d bars — aborting", symbol, len(bars))
        return BacktestResult(symbol=symbol)

    from strategies.engine import StrategyEngine
    engine = StrategyEngine(min_votes=min_votes)

    # Detect opening range close index (first bar after 9:30 AM)
    or_close_idx = _find_or_close_idx(bars)

    trades: list[Trade] = []
    signals_at: dict[int, object] = {}
    open_trade: Trade | None = None

    for i in range(50, len(bars)):
        ohlcv = _build_ohlcv_snapshot(bars, i, or_close_idx)

        # Check exits first
        if open_trade and not open_trade.closed:
            bar = bars[i]
            if open_trade.action == "BUY":
                if bar["h"] >= open_trade.target:
                    _close_trade(open_trade, open_trade.target, i, "TARGET")
                    trades.append(open_trade)
                    open_trade = None
                elif bar["l"] <= open_trade.stop_loss:
                    _close_trade(open_trade, open_trade.stop_loss, i, "STOP")
                    trades.append(open_trade)
                    open_trade = None
            else:  # SELL
                if bar["l"] <= open_trade.target:
                    _close_trade(open_trade, open_trade.target, i, "TARGET")
                    trades.append(open_trade)
                    open_trade = None
                elif bar["h"] >= open_trade.stop_loss:
                    _close_trade(open_trade, open_trade.stop_loss, i, "STOP")
                    trades.append(open_trade)
                    open_trade = None

        # Look for new signal (no open position, during trading hours)
        if open_trade is None and _is_trading_bar(bars[i]):
            try:
                sig = engine.evaluate(symbol, ohlcv, use_llm=False)
                if sig.action != "HOLD" and sig.combined_confidence >= 0.70:
                    signals_at[i] = sig
                    open_trade = Trade(
                        symbol=symbol,
                        action=sig.action,
                        entry=bars[i]["c"],
                        entry_idx=i,
                        stop_loss=sig.stop_loss,
                        target=sig.target,
                    )
            except Exception as exc:
                logger.debug("[Backtest] %s bar %d: %s", symbol, i, exc)

    # Force-close any open trade at last bar
    if open_trade and not open_trade.closed:
        _close_trade(open_trade, bars[-1]["c"], len(bars) - 1, "EOD")
        trades.append(open_trade)

    result = _compute_metrics(symbol, trades, signals_at)

    # Build chart
    try:
        from claude_trader.chart_builder import build_backtest_chart
        chart_path = build_backtest_chart(symbol, bars, signals_at,
                                          [{"pnl": t.pnl, "exit_idx": t.exit_idx} for t in trades])
        result.chart_path = str(chart_path)
    except Exception as exc:
        logger.warning("[Backtest] Chart build failed: %s", exc)

    logger.info("[Backtest] %s — %d trades, win=%.0f%%, P&L=%.1f",
                symbol, result.n_trades, result.win_rate * 100, result.total_pnl)
    return result


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fetch_bars(symbol: str, days: int) -> list[dict]:
    import yfinance as yf
    ticker = symbol if symbol.endswith(".NS") else symbol + ".NS"
    df = yf.download(ticker, interval="5m", period=f"{days}d", progress=False)
    if df.empty:
        logger.warning("[Backtest] yfinance returned empty data for %s", symbol)
        return []
    bars = []
    for ts, row in df.iterrows():
        bars.append({
            "t": ts.isoformat(),
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"]),
            "v": float(row["Volume"]),
        })
    return bars


def _build_ohlcv_snapshot(bars: list[dict], idx: int, or_close_idx: int) -> dict:
    """Build the same OHLCV dict the StrategyEngine expects, using only bars[:idx+1]."""
    window = bars[:idx + 1]
    closes = [b["c"] for b in window]
    highs  = [b["h"] for b in window]
    lows   = [b["l"] for b in window]
    last   = window[-1]["c"]

    # Opening range
    or_window = [b for b in window if _is_or_bar(b)]
    or_high   = max((b["h"] for b in or_window), default=None) if idx >= or_close_idx else None
    or_low    = min((b["l"] for b in or_window), default=None) if idx >= or_close_idx else None

    # Previous day levels (first bar's open as proxy)
    prev_close = window[0]["o"] if window else last

    # Indicators
    rsi = _compute_rsi(window)
    vwap = _compute_vwap(window)
    atr  = _compute_atr(window)

    # EMAs
    ema9 = ema21 = ema50 = ema200 = closes[0]
    for c in closes:
        ema9   = _ema_update(ema9,   c, 9)
        ema21  = _ema_update(ema21,  c, 21)
        ema50  = _ema_update(ema50,  c, 50)
        ema200 = _ema_update(ema200, c, 200)

    # MACD
    ema12 = ema26 = signal_ema = closes[0]
    for c in closes:
        ema12      = _ema_update(ema12, c, 12)
        ema26      = _ema_update(ema26, c, 26)
    macd_hist      = ema12 - ema26
    prev_macd_hist = macd_hist  # simplified
    macd_crossover = bool(macd_hist > 0 and prev_macd_hist <= 0)

    # Bollinger Bands
    if len(closes) >= 20:
        import statistics
        bb_mid = sum(closes[-20:]) / 20
        bb_std = statistics.stdev(closes[-20:])
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_pct   = (last - bb_lower) / max(bb_upper - bb_lower, 0.01)
    else:
        bb_pct = 0.5

    # Volume ratio
    recent_vols = [b.get("v", 0) for b in window[-20:]]
    prev_vols   = [b.get("v", 0) for b in window[-40:-20]] if len(window) >= 40 else recent_vols
    vol_ratio   = sum(recent_vols) / max(sum(prev_vols), 1)

    return {
        "last_close":      last,
        "session_open":    window[0]["o"],
        "rsi":             rsi,
        "macd_hist":       macd_hist,
        "macd_crossover":  macd_crossover,
        "above_ema200":    last > ema200,
        "above_ema20":     last > ema21,
        "vol_ratio":       round(vol_ratio, 2),
        "bb_pct":          round(bb_pct, 3),
        "vwap":            vwap,
        "atr":             atr,
        "or_high":         or_high,
        "or_low":          or_low,
        "prev_day_high":   max(highs),
        "prev_day_low":    min(lows),
        "prev_day_close":  prev_close,
        "candles":         window[-50:],
    }


def _is_or_bar(bar: dict) -> bool:
    """True if bar falls in opening range window (9:15–9:30 AM IST)."""
    ts = bar.get("t", "")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
        h, m = dt.hour, dt.minute
        return (9, 15) <= (h, m) < (9, 30)
    except Exception:
        return False


def _is_trading_bar(bar: dict) -> bool:
    ts = bar.get("t", "")
    if not ts:
        return True   # unknown time → allow
    try:
        dt = datetime.fromisoformat(ts)
        h, m = dt.hour, dt.minute
        return (9, 30) <= (h, m) < (15, 0)
    except Exception:
        return True


def _find_or_close_idx(bars: list[dict]) -> int:
    for i, b in enumerate(bars):
        ts = b.get("t", "")
        try:
            dt = datetime.fromisoformat(ts)
            if dt.hour > 9 or (dt.hour == 9 and dt.minute >= 30):
                return i
        except Exception:
            pass
    return 0


def _close_trade(trade: Trade, price: float, idx: int, reason: str) -> None:
    trade.exit_price  = price
    trade.exit_idx    = idx
    trade.exit_reason = reason
    trade.closed      = True
    if trade.action == "BUY":
        trade.pnl = (price - trade.entry) * trade.qty
    else:
        trade.pnl = (trade.entry - price) * trade.qty


def _compute_metrics(symbol: str, trades: list[Trade], signals_at: dict) -> BacktestResult:
    if not trades:
        return BacktestResult(symbol=symbol)

    pnls   = [t.pnl for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # Max drawdown
    running = 0.0
    peak    = 0.0
    max_dd  = 0.0
    for p in pnls:
        running += p
        peak    = max(peak, running)
        max_dd  = min(max_dd, running - peak)

    return BacktestResult(
        symbol=symbol,
        trades=trades,
        signals_at=signals_at,
        win_rate=len(wins) / len(trades),
        total_pnl=sum(pnls),
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        max_drawdown=max_dd,
        n_trades=len(trades),
    )
