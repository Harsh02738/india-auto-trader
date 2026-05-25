"""
Candlestick chart builder using mplfinance.

Two modes:
  build_live_chart   — current session bars with strategy signal overlay
  build_backtest_chart — full historical period with signal arrows + P&L curve
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")   # non-interactive backend — safe for server/cron
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import mplfinance as mpf
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

IST        = ZoneInfo("Asia/Kolkata")
CHART_DIR  = Path("data/charts")
BT_DIR     = Path("data/backtest")
CHART_DIR.mkdir(parents=True, exist_ok=True)
BT_DIR.mkdir(parents=True, exist_ok=True)


def _hhmm() -> str:
    return datetime.now(IST).strftime("%H%M")


# ── Live chart ─────────────────────────────────────────────────────────────────

def build_live_chart(
    symbol: str,
    bars: list[dict],
    ohlcv_meta: dict,
    consensus=None,         # ConsensusSignal | None
) -> Path:
    """
    Build a live session candlestick chart and save to data/charts/.
    Returns the PNG path.
    """
    if not bars or len(bars) < 3:
        logger.warning("[Chart] %s: too few bars (%d) — skipping", symbol, len(bars))
        return _placeholder(symbol)

    df = _bars_to_df(bars)
    addplots, panel_ratios = _live_addplots(df, ohlcv_meta)

    mc = mpf.make_marketcolors(up="#26a69a", down="#ef5350", inherit=True)
    style = mpf.make_mpf_style(marketcolors=mc, gridstyle="--", gridcolor="#e0e0e0")

    action   = consensus.action if consensus else "HOLD"
    votes    = f"{consensus.vote_count}/{consensus.total_strategies}" if consensus else ""
    conf     = f"{consensus.combined_confidence:.0%}" if consensus else ""
    title    = f"{symbol}  {ohlcv_meta.get('last_close','?')}  |  {action} {votes} Conf:{conf}  |  {datetime.now(IST).strftime('%H:%M IST')}"

    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        title=title,
        ylabel="Price (₹)",
        addplot=addplots,
        panel_ratios=panel_ratios,
        volume=False,        # volume handled in addplot panel
        returnfig=True,
        figsize=(14, 8),
        tight_layout=True,
    )

    # Signal arrow on last bar
    _draw_signal_arrow(axes[0], df, action)

    # Key horizontal levels
    _draw_levels(axes[0], ohlcv_meta)

    path = CHART_DIR / f"{symbol}_{_hhmm()}.png"
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    logger.info("[Chart] Saved live chart: %s", path)
    return path


def _live_addplots(df: pd.DataFrame, meta: dict) -> tuple[list, tuple]:
    closes = df["Close"]
    n = len(closes)
    addplots = []

    # EMA-9 and EMA-21
    ema9  = closes.ewm(span=9,  adjust=False).mean()
    ema21 = closes.ewm(span=21, adjust=False).mean()
    addplots.append(mpf.make_addplot(ema9,  color="#FF8C00", width=1.2, panel=0))
    addplots.append(mpf.make_addplot(ema21, color="#1565C0", width=1.2, panel=0))

    # VWAP from meta (single horizontal value → broadcast)
    vwap = meta.get("vwap")
    if vwap and vwap > 0:
        addplots.append(mpf.make_addplot(
            pd.Series([vwap] * n, index=df.index),
            color="#7B1FA2", linestyle="--", width=1.0, panel=0
        ))

    # Bollinger Bands (20-period)
    if n >= 20:
        mid  = closes.rolling(20).mean()
        std  = closes.rolling(20).std()
        addplots.append(mpf.make_addplot(mid + 2 * std, color="#9E9E9E", width=0.8, panel=0))
        addplots.append(mpf.make_addplot(mid - 2 * std, color="#9E9E9E", width=0.8, panel=0))

    # RSI panel
    rsi = _compute_rsi_series(closes)
    addplots.append(mpf.make_addplot(rsi, panel=1, color="#E65100", ylabel="RSI", ylim=(0, 100)))
    addplots.append(mpf.make_addplot(pd.Series([70] * n, index=df.index), panel=1, color="#B71C1C", linestyle="--", width=0.8))
    addplots.append(mpf.make_addplot(pd.Series([30] * n, index=df.index), panel=1, color="#1B5E20", linestyle="--", width=0.8))

    # Volume panel
    vol = df["Volume"].fillna(0)
    colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(df["Close"], df["Open"])]
    addplots.append(mpf.make_addplot(vol, type="bar", panel=2, color=colors, ylabel="Vol"))

    return addplots, (3, 1, 1)


def _draw_signal_arrow(ax, df: pd.DataFrame, action: str) -> None:
    if action == "HOLD" or df.empty:
        return
    last_x = len(df) - 1
    last_price = df["Close"].iloc[-1]
    if action == "BUY":
        ax.annotate("▲ BUY", xy=(last_x, last_price), fontsize=11,
                    color="#1B5E20", fontweight="bold",
                    xytext=(last_x - 1, last_price * 0.993),
                    arrowprops=dict(arrowstyle="->", color="#1B5E20"))
    elif action == "SELL":
        ax.annotate("▼ SELL", xy=(last_x, last_price), fontsize=11,
                    color="#B71C1C", fontweight="bold",
                    xytext=(last_x - 1, last_price * 1.007),
                    arrowprops=dict(arrowstyle="->", color="#B71C1C"))


def _draw_levels(ax, meta: dict) -> None:
    ylim = ax.get_ylim()
    def hline(price, color, label):
        if price and ylim[0] < price < ylim[1]:
            ax.axhline(y=price, color=color, linestyle="--", linewidth=0.8, alpha=0.7)
            ax.text(0.01, price, f" {label} ₹{price:.1f}", transform=ax.get_yaxis_transform(),
                    fontsize=7, color=color, va="bottom")

    hline(meta.get("or_high"),       "#2E7D32", "OR High")
    hline(meta.get("or_low"),        "#C62828", "OR Low")
    hline(meta.get("prev_day_close"), "#546E7A", "Prev Close")


# ── Backtest chart ─────────────────────────────────────────────────────────────

def build_backtest_chart(
    symbol: str,
    bars: list[dict],
    signals_at: dict[int, Any],   # idx → ConsensusSignal
    trades: list[dict],
) -> Path:
    """
    Build a backtest results chart with signal arrows + P&L curve.
    Returns the PNG path.
    """
    if not bars or len(bars) < 10:
        return _placeholder(symbol)

    df = _bars_to_df(bars)
    n  = len(df)

    # Build signal series
    buy_markers  = pd.Series([np.nan] * n, index=df.index, dtype=float)
    sell_markers = pd.Series([np.nan] * n, index=df.index, dtype=float)
    for idx, sig in signals_at.items():
        if idx < n:
            if sig.action == "BUY":
                buy_markers.iloc[idx] = df["Low"].iloc[idx] * 0.998
            elif sig.action == "SELL":
                sell_markers.iloc[idx] = df["High"].iloc[idx] * 1.002

    # Cumulative P&L curve
    pnl_curve = pd.Series([0.0] * n, index=df.index, dtype=float)
    running = 0.0
    for t in sorted(trades, key=lambda x: x.get("exit_idx", 0)):
        ei = t.get("exit_idx", 0)
        if 0 <= ei < n:
            running += t.get("pnl", 0.0)
            pnl_curve.iloc[ei] = running
    # forward fill so curve is continuous
    pnl_curve = pnl_curve.replace(0, np.nan).ffill().fillna(0)

    addplots = []
    ema9  = df["Close"].ewm(span=9,  adjust=False).mean()
    ema21 = df["Close"].ewm(span=21, adjust=False).mean()
    addplots.append(mpf.make_addplot(ema9,  color="#FF8C00", width=1.0, panel=0))
    addplots.append(mpf.make_addplot(ema21, color="#1565C0", width=1.0, panel=0))

    if not buy_markers.isna().all():
        addplots.append(mpf.make_addplot(buy_markers,  type="scatter", markersize=80,
                                          marker="^", color="#1B5E20", panel=0))
    if not sell_markers.isna().all():
        addplots.append(mpf.make_addplot(sell_markers, type="scatter", markersize=80,
                                          marker="v", color="#B71C1C", panel=0))

    rsi = _compute_rsi_series(df["Close"])
    addplots.append(mpf.make_addplot(rsi, panel=1, color="#E65100", ylabel="RSI", ylim=(0, 100)))
    addplots.append(mpf.make_addplot(pnl_curve, panel=2, color="#0D47A1", ylabel="Cum P&L (pts)"))

    n_trades  = len(trades)
    wins      = sum(1 for t in trades if t.get("pnl", 0) > 0)
    win_rate  = wins / max(n_trades, 1)
    total_pnl = sum(t.get("pnl", 0) for t in trades)

    mc    = mpf.make_marketcolors(up="#26a69a", down="#ef5350", inherit=True)
    style = mpf.make_mpf_style(marketcolors=mc, gridstyle="--", gridcolor="#e0e0e0")
    title = f"{symbol} Backtest — Win:{win_rate:.0%} | P&L:{total_pnl:+.1f}pts | {n_trades} trades"

    fig, _ = mpf.plot(
        df, type="candle", style=style, title=title,
        ylabel="Price (₹)", addplot=addplots,
        panel_ratios=(3, 1, 1), volume=False,
        returnfig=True, figsize=(16, 9), tight_layout=True,
    )

    path = BT_DIR / f"{symbol}_{datetime.now(IST).strftime('%Y-%m-%d')}.png"
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    logger.info("[Chart] Saved backtest chart: %s", path)
    return path


# ── Helpers ────────────────────────────────────────────────────────────────────

def _bars_to_df(bars: list[dict]) -> pd.DataFrame:
    rows = []
    for b in bars:
        ts = b.get("t") or b.get("timestamp") or b.get("time")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except Exception:
                ts = datetime.now(IST)
        rows.append({
            "Date": ts,
            "Open":   float(b.get("o") or b.get("open",  0)),
            "High":   float(b.get("h") or b.get("high",  0)),
            "Low":    float(b.get("l") or b.get("low",   0)),
            "Close":  float(b.get("c") or b.get("close", 0)),
            "Volume": float(b.get("v") or b.get("volume", 0)),
        })
    df = pd.DataFrame(rows).set_index("Date")
    df.index = pd.DatetimeIndex(df.index)
    return df.sort_index()


def _compute_rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def _placeholder(symbol: str) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.text(0.5, 0.5, f"{symbol}: insufficient data", ha="center", va="center", fontsize=14)
    ax.axis("off")
    path = CHART_DIR / f"{symbol}_{_hhmm()}_placeholder.png"
    fig.savefig(path, dpi=80)
    plt.close(fig)
    return path
