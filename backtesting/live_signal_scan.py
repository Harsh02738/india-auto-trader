"""
Live MACD+RSI signal scanner for a given symbol list.
Usage: python -m backtesting.live_signal_scan [--symbols A,B,C]
"""
from __future__ import annotations

import argparse
import sys
import yfinance as yf
import pandas as pd
import numpy as np

from backtesting.strategy_backtester import YFINANCE_SYMBOL_MAP

DEFAULT_SYMBOLS = [
    "INDUSINDBK", "BOSCHLTD", "ULTRACEMCO", "EICHERMOT",
    "TVSMOTOR", "BAJAJFINSV", "LEMONTREE", "IDFCFIRSTB",
]


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def scan(symbols: list[str]) -> None:
    header = f"{'Symbol':<14} {'Price':>8} {'RSI':>6} {'Hist':>7} {'PrevH':>7} {'EMA200':>7} {'VolRx':>6} {'BB%':>5}  Signal"
    print(header)
    print("-" * len(header))

    for sym in symbols:
        yf_sym = YFINANCE_SYMBOL_MAP.get(sym, sym)
        df = yf.Ticker(f"{yf_sym}.NS").history(period="2y", interval="1d", auto_adjust=True)
        if len(df) < 210:
            print(f"{sym:<14} -- insufficient data ({len(df)} rows)")
            continue

        df = df.rename(columns={"Open": "O", "High": "H", "Low": "L", "Close": "C", "Volume": "V"})

        macd_line = _ema(df.C, 12) - _ema(df.C, 26)
        sig_line  = _ema(macd_line, 9)
        hist      = macd_line - sig_line
        rsi14     = _rsi(df.C)
        ema200    = _ema(df.C, 200)
        vol_avg20 = df.V.rolling(20).mean()
        vol_ratio = df.V / vol_avg20
        sma20     = df.C.rolling(20).mean()
        std20     = df.C.rolling(20).std()
        bb_range  = (sma20 + 2 * std20) - (sma20 - 2 * std20)
        bb_pct    = (df.C - (sma20 - 2 * std20)) / bb_range.replace(0, np.nan)

        # Drop trailing rows with NaN close (partial today data)
        df_clean = df.dropna(subset=["C"])
        if len(df_clean) < 210:
            print(f"{sym:<14} -- insufficient valid rows ({len(df_clean)})")
            continue

        last_idx = df_clean.index[-1]
        prev_idx = df_clean.index[-2]

        price      = round(float(df_clean.C.loc[last_idx]), 2)
        rsi_now    = round(float(rsi14.loc[last_idx]), 1)
        hist_now   = round(float(hist.loc[last_idx]), 3)
        hist_prev  = round(float(hist.loc[prev_idx]), 3)
        above_ema  = bool(df_clean.C.loc[last_idx] > ema200.loc[last_idx])
        vol_rx     = round(float(vol_ratio.loc[last_idx]), 2)
        bb_now     = round(float(bb_pct.loc[last_idx]), 2)

        macd_cross = hist_prev < 0 < hist_now
        rsi_ok     = 25 <= rsi_now <= 45

        if macd_cross and rsi_ok:
            signal = "*** BUY SIGNAL ***"
        elif hist_now > 0 and rsi_ok:
            signal = "MACD+ / RSI-ok (no fresh cross)"
        elif rsi_now < 35 and hist_now > -1.0:
            signal = "RSI oversold - watch"
        else:
            signal = "-"

        ema_label = "YES" if above_ema else "no"
        print(
            f"{sym:<14} {price:>8.2f} {rsi_now:>6.1f} {hist_now:>7.3f} {hist_prev:>7.3f}"
            f" {ema_label:>7} {vol_rx:>6.2f} {bb_now:>5.2f}  {signal}"
        )

    print()
    print("macd_cross = histogram crossed 0 from below (prev<0, now>0)")
    print("RSI ok     = 25-45 (rising from oversold base)")
    print("BUY SIGNAL = full MACD+RSI confluence")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live MACD+RSI signal scanner")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS),
                        help="Comma-separated symbol list")
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    scan(symbols)


if __name__ == "__main__":
    main()
