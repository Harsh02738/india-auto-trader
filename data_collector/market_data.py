"""
Collects OHLCV data via yfinance and computes all technical indicators.
Writes data/market/{SYMBOL}_ohlcv.json every 5 minutes during market hours.
"""

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from config.settings import settings

logger = logging.getLogger(__name__)

DATA_DIR = Path("data/market")
DATA_DIR.mkdir(parents=True, exist_ok=True)

IST = timezone(pd.Timedelta(hours=5, minutes=30))


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = _ema(close, fast)
    slow_ema = _ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(close: pd.Series, period: int, std_mult: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def _volume_ratio(volume: pd.Series, period: int) -> pd.Series:
    avg = volume.rolling(period).mean()
    return (volume / avg.replace(0, np.nan)).round(2)


def _safe(val) -> float | None:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return round(float(val), 4)


# ── Main collector ─────────────────────────────────────────────────────────────

def collect_ohlcv(symbol: str, period: str = "3mo", interval: str = "1d") -> dict:
    """
    Download OHLCV from yfinance, compute indicators, write JSON.
    Returns the payload dict.

    symbol: NSE symbol (e.g. "RELIANCE")
    period: yfinance period string
    interval: yfinance interval string
    """
    ticker_symbol = f"{symbol}.NS"
    logger.info("Fetching %s (period=%s interval=%s)", ticker_symbol, period, interval)

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
    except Exception as exc:
        logger.error("yfinance error for %s: %s", symbol, exc)
        return {}

    if df.empty or len(df) < settings.ema_long + 10:
        logger.warning("Insufficient data for %s (%d rows)", symbol, len(df))
        return {}

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    # Indicators
    rsi = _rsi(close, settings.rsi_period)
    macd_line, macd_sig, macd_hist = _macd(close, settings.macd_fast, settings.macd_slow, settings.macd_signal)
    bb_upper, bb_mid, bb_lower = _bollinger(close, settings.bb_period, settings.bb_std)
    ema20  = _ema(close, settings.ema_short)
    ema50  = _ema(close, settings.ema_mid)
    ema200 = _ema(close, settings.ema_long)
    atr    = _atr(high, low, close, settings.atr_period)
    vol_ratio = _volume_ratio(vol, settings.volume_avg_period)

    # Derived signals
    last_close  = float(close.iloc[-1])
    last_rsi    = float(rsi.iloc[-1])
    last_macd   = float(macd_line.iloc[-1])
    last_msig   = float(macd_sig.iloc[-1])
    last_mhist  = float(macd_hist.iloc[-1])
    prev_mhist  = float(macd_hist.iloc[-2]) if len(macd_hist) > 1 else last_mhist

    macd_crossover = (last_mhist > 0) and (prev_mhist <= 0)   # bullish MACD cross
    macd_crossunder = (last_mhist < 0) and (prev_mhist >= 0)  # bearish MACD cross

    above_ema200 = last_close > float(ema200.iloc[-1])
    above_ema50  = last_close > float(ema50.iloc[-1])
    above_ema20  = last_close > float(ema20.iloc[-1])

    bb_upper_val = float(bb_upper.iloc[-1])
    bb_lower_val = float(bb_lower.iloc[-1])
    bb_pct = (last_close - bb_lower_val) / max(bb_upper_val - bb_lower_val, 0.0001)

    # 52-week high/low from close
    year_high = float(close.rolling(252).max().iloc[-1])
    year_low  = float(close.rolling(252).min().iloc[-1])

    # Build candle rows (last 60 bars for frontend chart)
    candles = []
    tail = df.tail(60)
    for ts, row in tail.iterrows():
        dt_str = ts.strftime("%Y-%m-%dT%H:%M:%S+05:30") if hasattr(ts, "strftime") else str(ts)
        candles.append({
            "t": dt_str,
            "o": round(float(row["Open"]), 2),
            "h": round(float(row["High"]), 2),
            "l": round(float(row["Low"]), 2),
            "c": round(float(row["Close"]), 2),
            "v": int(row["Volume"]),
        })

    payload = {
        "symbol":      symbol,
        "exchange":    "NSE",
        "timestamp":   datetime.now(tz=timezone.utc).isoformat(),
        "interval":    interval,

        # Latest price snapshot
        "last_close":  round(last_close, 2),
        "year_high":   round(year_high, 2),
        "year_low":    round(year_low, 2),
        "pct_from_52w_high": round((last_close - year_high) / year_high * 100, 2),

        # Volume
        "last_volume":    int(vol.iloc[-1]),
        "vol_ratio":      _safe(vol_ratio.iloc[-1]),
        "avg_volume_20d": int(vol.tail(settings.volume_avg_period).mean()),

        # RSI
        "rsi":            _safe(last_rsi),
        "rsi_signal":     "OVERSOLD" if last_rsi < 30 else "OVERBOUGHT" if last_rsi > 70 else "NEUTRAL",

        # MACD
        "macd":           _safe(last_macd),
        "macd_signal":    _safe(last_msig),
        "macd_hist":      _safe(last_mhist),
        "macd_crossover": macd_crossover,
        "macd_crossunder": macd_crossunder,

        # Bollinger Bands
        "bb_upper":  _safe(bb_upper_val),
        "bb_mid":    _safe(float(bb_mid.iloc[-1])),
        "bb_lower":  _safe(bb_lower_val),
        "bb_pct":    _safe(bb_pct),          # 0=at lower, 1=at upper
        "bb_squeeze": _safe(float(atr.iloc[-1]) / max(bb_upper_val - bb_lower_val, 0.0001)),

        # EMAs
        "ema20":     _safe(float(ema20.iloc[-1])),
        "ema50":     _safe(float(ema50.iloc[-1])),
        "ema200":    _safe(float(ema200.iloc[-1])),
        "above_ema20":  above_ema20,
        "above_ema50":  above_ema50,
        "above_ema200": above_ema200,

        # ATR
        "atr":       _safe(float(atr.iloc[-1])),
        "atr_pct":   _safe(float(atr.iloc[-1]) / last_close * 100),

        # Chart candles
        "candles":   candles,
    }

    out_path = DATA_DIR / f"{symbol}_ohlcv.json"
    out_path.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote %s (%d candles)", out_path, len(candles))
    return payload


def collect_intraday(symbol: str) -> dict:
    """Collect 5-min intraday bars for today (yfinance 1d/5m)."""
    return collect_ohlcv(symbol, period="1d", interval="5m")


def collect_daily(symbol: str) -> dict:
    """Collect daily OHLCV for 3 months (standard baseline)."""
    return collect_ohlcv(symbol, period="3mo", interval="1d")


def run(symbols: list[str]) -> dict[str, dict]:
    """Collect daily data for a list of symbols."""
    results: dict[str, dict] = {}
    for sym in symbols:
        try:
            results[sym] = collect_daily(sym)
        except Exception as exc:
            logger.error("Failed %s: %s", sym, exc)
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    syms = sys.argv[1:] or ["RELIANCE", "TCS", "HDFCBANK"]
    run(syms)
