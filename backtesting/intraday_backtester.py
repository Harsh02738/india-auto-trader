"""
Intraday backtester for high-beta NSE stocks.

Fetches 5m (or 1m) data from yfinance, respecting per-request limits:
  - 1m : last 30 days, max 7 calendar days per request → auto-chunked
  - 5m : last 60 days, full range in a single request

Runs all 4 intraday strategies + consensus engine pass (≥2 votes, same as live).

Usage:
    python -m backtesting.intraday_backtester --week
    python -m backtesting.intraday_backtester --week --interval 1m
    python -m backtesting.intraday_backtester --symbols TATAMOTORS,INDUSINDBK --interval 5m --week
    python -m backtesting.intraday_backtester --start 2026-05-12 --end 2026-05-23
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from backtesting.metrics import compute_metrics, PerformanceMetrics
from strategies.base import BaseStrategy, StrategySignal
from strategies.mean_reversion import MeanReversionStrategy
from strategies.macd_rsi_confluence import MacdRsiConfluenceStrategy
from strategies.supertrend import SupertrendStrategy
from strategies.vwap_reversion import VwapReversionStrategy
from strategies.engine import StrategyEngine

logger = logging.getLogger(__name__)

# ── Universe ──────────────────────────────────────────────────────────────────

# Curated from sectors with historically high beta vs Nifty (> 1.2)
HIGH_BETA_STOCKS: list[str] = [
    # Auto — cyclical, high beta
    "TATAMOTORS", "M&M", "EICHERMOT", "TVSMOTOR", "HEROMOTOCO", "MARUTI",
    # Banks with higher volatility (large-cap + mid-cap)
    "INDUSINDBK", "IDFCFIRSTB", "BANKBARODA", "FEDERALBNK",
    "HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN",
    # Metals — commodity-linked, reactive to macro
    "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL",
    # NBFC / fintech
    "BAJFINANCE", "SBICARD", "BAJAJFINSV", "CHOLAFIN",
    # Conglomerate / Infrastructure
    "ADANIENT", "ADANIPORTS",
    # Defence
    "BEL", "HAL",
    # Energy — macro-reactive, very liquid
    "RELIANCE",
    # IT — different correlation from banking/auto
    "HCLTECH",
    # Capital Goods — infra cycle, beta ~1.2
    "LT",
]

# NSE symbol → Yahoo Finance ticker override (before appending .NS)
YFINANCE_SYMBOL_MAP: dict[str, str] = {
    "INFOSYS": "INFY",
}

# Only intraday strategies — Momentum (ROC-20 swing) and BollingerSqueeze
# (6-month squeeze) are excluded as they are meaningless on intraday bars.
INTRADAY_STRATEGIES: list[BaseStrategy] = [
    VwapReversionStrategy(),
    MacdRsiConfluenceStrategy(),
    MeanReversionStrategy(),
    SupertrendStrategy(),
]

STRATEGY_MAP: dict[str, BaseStrategy] = {
    s.name.lower().replace(" ", "").replace("+", "").replace("-", ""): s
    for s in INTRADAY_STRATEGIES
}

# Calendar days of warmup data fetched before start_date for indicator seeding
_LOOKBACK_CALENDAR_DAYS: dict[str, int] = {
    "1m": 14,
    "2m": 20,
    "5m": 20,
    "15m": 30,
    "30m": 30,
    "1h": 30,
}


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_intraday_chunked(
    symbol: str,
    interval: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame | None:
    """
    Fetch intraday OHLCV, handling yfinance interval limits:
      - 1m: max 7 calendar days per request → auto-chunked across the range
      - 5m / others: single request (within 60-day limit)
    Cached to data/backtest_cache/ as Parquet.
    """
    yf_sym = YFINANCE_SYMBOL_MAP.get(symbol, symbol)
    cache_key = f"{symbol}_{interval}_{start_date}_{end_date}"
    cache_path = Path("data/backtest_cache") / f"{cache_key}.parquet"

    if cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except Exception:
            pass

    end_dt = date.fromisoformat(end_date) + timedelta(days=1)

    try:
        ticker = yf.Ticker(f"{yf_sym}.NS")

        if interval == "1m":
            start_dt = date.fromisoformat(start_date)
            chunks: list[pd.DataFrame] = []
            chunk_start = start_dt
            while chunk_start < end_dt:
                chunk_end = min(chunk_start + timedelta(days=7), end_dt)
                chunk = ticker.history(
                    start=chunk_start.isoformat(),
                    end=chunk_end.isoformat(),
                    interval="1m",
                    auto_adjust=True,
                )
                if not chunk.empty:
                    chunks.append(chunk)
                chunk_start = chunk_end
            if not chunks:
                return None
            df = pd.concat(chunks)
            df = df[~df.index.duplicated(keep="first")].sort_index()
        else:
            df = ticker.history(
                start=start_date,
                end=end_dt.isoformat(),
                interval=interval,
                auto_adjust=True,
            )

        if df is None or df.empty:
            return None

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.to_parquet(cache_path)
        except Exception:
            pass
        return df

    except Exception as exc:
        logger.debug("Fetch error %s %s: %s", symbol, interval, exc)
        return None


def _fetch_daily(symbol: str) -> pd.DataFrame | None:
    """Fetch 1-year daily bars for EMA-200 structural trend context."""
    yf_sym = YFINANCE_SYMBOL_MAP.get(symbol, symbol)
    cache_path = Path("data/backtest_cache") / f"{symbol}_1d_1y.parquet"

    if cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except Exception:
            pass

    try:
        df = yf.Ticker(f"{yf_sym}.NS").history(period="1y", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.to_parquet(cache_path)
        except Exception:
            pass
        return df
    except Exception as exc:
        logger.debug("Daily fetch error %s: %s", symbol, exc)
        return None


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class IntradayTrade:
    symbol:       str
    strategy:     str
    date:         str
    entry_time:   str
    exit_time:    str
    entry_price:  float
    exit_price:   float
    pnl_pct:      float
    pnl_abs:      float
    holding_bars: int
    exit_reason:  str   # "target" | "stop_loss" | "session_end"


@dataclass
class IntradayBacktestResult:
    strategy_name:  str
    interval:       str
    date_range:     str
    symbols_tested: int
    total_trades:   int
    metrics:        PerformanceMetrics
    trades:         list[IntradayTrade] = field(default_factory=list)


# ── Backtester ────────────────────────────────────────────────────────────────

class IntradayBacktester:
    """
    Simulates intraday strategy execution on 1m or 5m bar data.

    Session rules (matching live MIS):
    - Skip first MIN_SESSION_BAR bars to avoid open-volatility whipsaws
    - No new entries after 14:30 IST
    - Force-close all open positions at 15:15 IST (before Kotak 15:20 auto-square)
    - One position per symbol per session
    """

    MIN_SESSION_BAR = 15   # skip first N bars per session (open volatility)
    LAST_ENTRY_HOUR = 14
    LAST_ENTRY_MIN  = 30   # no entries after 14:30
    CLOSE_HOUR      = 15
    CLOSE_MIN       = 15   # force-close at 15:15
    MIN_CONFIDENCE  = 0.45 # minimum signal confidence to enter

    def run(
        self,
        strategies: list[BaseStrategy],
        symbols: list[str],
        interval: str,
        start_date: str,
        end_date: str,
        run_consensus: bool = True,
    ) -> list[IntradayBacktestResult]:
        date_range = f"{start_date}_to_{end_date}"
        lookback_days = _LOOKBACK_CALENDAR_DAYS.get(interval, 20)
        lookback_start = (
            date.fromisoformat(start_date) - timedelta(days=lookback_days)
        ).isoformat()

        results: list[IntradayBacktestResult] = []

        for strat in strategies:
            all_pnls:   list[float] = []
            all_bars:   list[float] = []
            all_trades: list[IntradayTrade] = []

            for symbol in symbols:
                try:
                    trades = self._backtest_symbol(
                        strat, symbol, interval, lookback_start, start_date, end_date
                    )
                    for t in trades:
                        all_pnls.append(t.pnl_pct)
                        all_bars.append(float(t.holding_bars))
                    all_trades.extend(trades)
                except Exception as exc:
                    logger.debug("Error %s/%s: %s", strat.name, symbol, exc)

            metrics = compute_metrics(all_pnls, all_bars) if all_pnls else _zero_metrics()
            results.append(IntradayBacktestResult(
                strategy_name=strat.name,
                interval=interval,
                date_range=date_range,
                symbols_tested=len(symbols),
                total_trades=len(all_pnls),
                metrics=metrics,
                trades=all_trades,
            ))
            logger.info("%s: %d trades across %d symbols", strat.name, len(all_pnls), len(symbols))

        if run_consensus:
            cr = self._run_consensus(
                symbols, interval, lookback_start, start_date, end_date, date_range
            )
            if cr is not None:
                results.append(cr)

        return results

    # ── Per-symbol single-strategy backtest ───────────────────────────────────

    def _backtest_symbol(
        self,
        strategy: BaseStrategy,
        symbol: str,
        interval: str,
        lookback_start: str,
        start_date: str,
        end_date: str,
    ) -> list[IntradayTrade]:
        full_df = fetch_intraday_chunked(symbol, interval, lookback_start, end_date)
        if full_df is None or full_df.empty:
            return []

        daily_df = _fetch_daily(symbol)
        full_df  = _to_ist(full_df)

        start_d = date.fromisoformat(start_date)
        end_d   = date.fromisoformat(end_date)

        pre_df  = full_df[full_df.index.date < start_d]
        test_df = full_df[
            (full_df.index.date >= start_d) & (full_df.index.date <= end_d)
        ]
        if test_df.empty:
            return []

        trades: list[IntradayTrade] = []

        for session_date in sorted(set(test_df.index.date)):
            day_df = test_df[test_df.index.date == session_date]
            try:
                day_df = day_df.between_time("09:15", "15:30")
            except Exception:
                pass
            if len(day_df) < self.MIN_SESSION_BAR + 2:
                continue

            above_ema200 = _daily_ema200(daily_df, session_date)
            prior = pd.concat([pre_df, test_df[test_df.index.date < session_date]])
            warmup = prior.iloc[-200:]   # cap warmup at 200 bars for speed

            trades.extend(
                self._simulate_session(strategy, symbol, day_df, warmup, above_ema200, session_date)
            )

        return trades

    def _simulate_session(
        self,
        strategy: BaseStrategy,
        symbol: str,
        day_df: pd.DataFrame,
        warmup: pd.DataFrame,
        above_ema200: bool,
        session_date,
    ) -> list[IntradayTrade]:
        # Pre-concatenate warmup + session once; slice into it each bar
        combined  = pd.concat([warmup, day_df])
        wlen      = len(warmup)
        day_rows  = list(day_df.itertuples())

        in_trade    = False
        entry_price = 0.0
        entry_time  = ""
        entry_bar   = 0
        stop_loss   = 0.0
        target      = 0.0
        session_trades: list[IntradayTrade] = []

        for i, bar in enumerate(day_rows):
            bar_time = bar.Index.time()
            close = float(bar.Close)
            high  = float(bar.High)
            low   = float(bar.Low)

            if in_trade:
                hit_target  = high >= target
                hit_stop    = low  <= stop_loss
                force_close = _past_cutoff(bar_time, self.CLOSE_HOUR, self.CLOSE_MIN)

                if hit_target or hit_stop or force_close:
                    if hit_target:
                        exit_price, reason = target,    "target"
                    elif hit_stop:
                        exit_price, reason = stop_loss, "stop_loss"
                    else:
                        exit_price, reason = close,     "session_end"

                    session_trades.append(IntradayTrade(
                        symbol=symbol,
                        strategy=strategy.name,
                        date=str(session_date),
                        entry_time=entry_time,
                        exit_time=bar.Index.strftime("%H:%M"),
                        entry_price=round(entry_price, 2),
                        exit_price=round(exit_price, 2),
                        pnl_pct=(exit_price - entry_price) / entry_price,
                        pnl_abs=round(exit_price - entry_price, 2),
                        holding_bars=i - entry_bar,
                        exit_reason=reason,
                    ))
                    in_trade = False
            else:
                if i < self.MIN_SESSION_BAR:
                    continue
                if _past_cutoff(bar_time, self.LAST_ENTRY_HOUR, self.LAST_ENTRY_MIN):
                    continue

                window    = combined.iloc[:wlen + i + 1]
                day_so_far = combined.iloc[wlen:wlen + i + 1]
                ohlcv = _build_ohlcv_dict(window, day_so_far, symbol, above_ema200)
                if not ohlcv:
                    continue

                try:
                    signal: StrategySignal = strategy.generate_signal(ohlcv)
                except Exception:
                    continue

                if signal.action == "BUY" and signal.confidence >= self.MIN_CONFIDENCE:
                    in_trade    = True
                    entry_price = close
                    entry_time  = bar.Index.strftime("%H:%M")
                    entry_bar   = i
                    stop_loss   = signal.stop_loss
                    target      = signal.target

        return session_trades

    # ── Consensus engine pass ─────────────────────────────────────────────────

    def _run_consensus(
        self,
        symbols: list[str],
        interval: str,
        lookback_start: str,
        start_date: str,
        end_date: str,
        date_range: str,
    ) -> IntradayBacktestResult | None:
        engine = StrategyEngine(min_votes=2)
        all_pnls:   list[float] = []
        all_bars:   list[float] = []
        all_trades: list[IntradayTrade] = []

        for symbol in symbols:
            try:
                trades = self._backtest_consensus_symbol(
                    engine, symbol, interval, lookback_start, start_date, end_date
                )
                for t in trades:
                    all_pnls.append(t.pnl_pct)
                    all_bars.append(float(t.holding_bars))
                all_trades.extend(trades)
            except Exception as exc:
                logger.debug("Consensus error %s: %s", symbol, exc)

        if not all_pnls:
            return None

        logger.info("Consensus(>=2): %d trades across %d symbols", len(all_pnls), len(symbols))
        return IntradayBacktestResult(
            strategy_name="Consensus(>=2)",
            interval=interval,
            date_range=date_range,
            symbols_tested=len(symbols),
            total_trades=len(all_pnls),
            metrics=compute_metrics(all_pnls, all_bars),
            trades=all_trades,
        )

    def _backtest_consensus_symbol(
        self,
        engine: StrategyEngine,
        symbol: str,
        interval: str,
        lookback_start: str,
        start_date: str,
        end_date: str,
    ) -> list[IntradayTrade]:
        full_df = fetch_intraday_chunked(symbol, interval, lookback_start, end_date)
        if full_df is None or full_df.empty:
            return []

        daily_df = _fetch_daily(symbol)
        full_df  = _to_ist(full_df)

        start_d = date.fromisoformat(start_date)
        end_d   = date.fromisoformat(end_date)

        pre_df  = full_df[full_df.index.date < start_d]
        test_df = full_df[
            (full_df.index.date >= start_d) & (full_df.index.date <= end_d)
        ]
        if test_df.empty:
            return []

        trades: list[IntradayTrade] = []

        for session_date in sorted(set(test_df.index.date)):
            day_df = test_df[test_df.index.date == session_date]
            try:
                day_df = day_df.between_time("09:15", "15:30")
            except Exception:
                pass
            if len(day_df) < self.MIN_SESSION_BAR + 2:
                continue

            above_ema200 = _daily_ema200(daily_df, session_date)
            prior  = pd.concat([pre_df, test_df[test_df.index.date < session_date]])
            warmup = prior.iloc[-200:]

            combined = pd.concat([warmup, day_df])
            wlen     = len(warmup)
            day_rows = list(day_df.itertuples())

            in_trade    = False
            entry_price = 0.0
            entry_time  = ""
            entry_bar   = 0
            stop_loss   = 0.0
            target      = 0.0

            for i, bar in enumerate(day_rows):
                bar_time = bar.Index.time()
                close = float(bar.Close)
                high  = float(bar.High)
                low   = float(bar.Low)

                if in_trade:
                    hit_target  = high >= target
                    hit_stop    = low  <= stop_loss
                    force_close = _past_cutoff(bar_time, self.CLOSE_HOUR, self.CLOSE_MIN)

                    if hit_target or hit_stop or force_close:
                        if hit_target:
                            exit_price, reason = target,    "target"
                        elif hit_stop:
                            exit_price, reason = stop_loss, "stop_loss"
                        else:
                            exit_price, reason = close,     "session_end"

                        trades.append(IntradayTrade(
                            symbol=symbol,
                            strategy="Consensus",
                            date=str(session_date),
                            entry_time=entry_time,
                            exit_time=bar.Index.strftime("%H:%M"),
                            entry_price=round(entry_price, 2),
                            exit_price=round(exit_price, 2),
                            pnl_pct=(exit_price - entry_price) / entry_price,
                            pnl_abs=round(exit_price - entry_price, 2),
                            holding_bars=i - entry_bar,
                            exit_reason=reason,
                        ))
                        in_trade = False
                else:
                    if i < self.MIN_SESSION_BAR:
                        continue
                    if _past_cutoff(bar_time, self.LAST_ENTRY_HOUR, self.LAST_ENTRY_MIN):
                        continue

                    window     = combined.iloc[:wlen + i + 1]
                    day_so_far = combined.iloc[wlen:wlen + i + 1]
                    ohlcv = _build_ohlcv_dict(window, day_so_far, symbol, above_ema200)
                    if not ohlcv:
                        continue

                    try:
                        consensus = engine.evaluate(symbol, ohlcv, use_llm=False)
                    except Exception:
                        continue

                    if consensus.action == "BUY" and consensus.vote_count >= 2:
                        in_trade    = True
                        entry_price = close
                        entry_time  = bar.Index.strftime("%H:%M")
                        entry_bar   = i
                        stop_loss   = consensus.stop_loss
                        target      = consensus.target

        return trades


# ── Module-level helpers ──────────────────────────────────────────────────────

def _to_ist(df: pd.DataFrame) -> pd.DataFrame:
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
    else:
        df.index = df.index.tz_convert("Asia/Kolkata")
    return df


def _past_cutoff(t, hour: int, minute: int) -> bool:
    return t.hour > hour or (t.hour == hour and t.minute >= minute)


def _daily_ema200(daily_df: pd.DataFrame | None, session_date) -> bool:
    """Return True if the last daily close before session_date was above EMA-200."""
    if daily_df is None or daily_df.empty:
        return True
    try:
        idx = daily_df.index
        if idx.tz is not None:
            idx = idx.tz_convert("Asia/Kolkata")
        dates = idx.date
        daily = daily_df["Close"][dates < session_date]
        if len(daily) < 50:
            return True
        ema200 = float(daily.ewm(span=200, adjust=False).mean().iloc[-1])
        return float(daily.iloc[-1]) > ema200
    except Exception:
        return True


def _build_ohlcv_dict(
    window_df: pd.DataFrame,
    day_candles_df: pd.DataFrame,
    symbol: str,
    above_ema200: bool,
) -> dict:
    """
    Build the ohlcv dict that all strategies expect.

    window_df      : multi-day window (warmup + today so far) for RSI / MACD / ATR / BB
    day_candles_df : current session bars only — passed as 'candles' so each
                     strategy's VWAP calculation resets at 09:15 each day.
    """
    if window_df.empty or len(window_df) < 14:
        return {}

    c  = window_df["Close"]
    h  = window_df["High"]
    lo = window_df["Low"]
    v  = window_df["Volume"]

    last_close = float(c.iloc[-1])

    # RSI-14 (Wilder smoothed)
    delta = c.diff()
    ag = delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
    al = (-delta).clip(lower=0).ewm(com=13, min_periods=14).mean()
    rs = ag / al.replace(0, float("nan"))
    rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if not rs.empty else 50.0

    # MACD (12 / 26 / 9)
    fast = c.ewm(span=12, adjust=False).mean()
    slow = c.ewm(span=26, adjust=False).mean()
    macd = fast - slow
    sig  = macd.ewm(span=9, adjust=False).mean()
    hist = macd - sig
    prev_hist  = float(hist.iloc[-2]) if len(hist) > 1 else 0.0
    last_hist  = float(hist.iloc[-1])
    macd_cross = (last_hist > 0) and (prev_hist <= 0)

    # EMA-20
    ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])

    # ATR-14 (Wilder smoothed)
    tr = pd.concat([
        h - lo,
        (h - c.shift()).abs(),
        (lo - c.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.ewm(com=13, min_periods=14).mean().iloc[-1])

    # Bollinger Bands (20-bar, 2σ)
    bb_m = c.rolling(20).mean()
    bb_s = c.rolling(20).std(ddof=0)
    bu   = float((bb_m + 2 * bb_s).iloc[-1])
    bl   = float((bb_m - 2 * bb_s).iloc[-1])
    bm   = float(bb_m.iloc[-1])
    bb_pct = (last_close - bl) / max(bu - bl, 1e-6)

    # Volume ratio (current vs 20-bar average)
    avg_vol   = v.rolling(20).mean().iloc[-1]
    vol_ratio = float(v.iloc[-1] / avg_vol) if avg_vol and avg_vol > 0 else 1.0

    # Candles — day-only so VWAP resets at session open
    candles = [
        {"h": float(r.High), "l": float(r.Low), "c": float(r.Close),
         "v": int(r.Volume), "o": float(r.Open)}
        for r in day_candles_df.itertuples()
    ]

    return {
        "symbol":         symbol,
        "last_close":     last_close,
        "rsi":            rsi,
        "macd_hist":      last_hist,
        "macd_crossover": macd_cross,
        "above_ema200":   above_ema200,
        "atr":            atr,
        "bb_upper":       bu,
        "bb_mid":         bm,
        "bb_lower":       bl,
        "bb_pct":         bb_pct,
        "bb_squeeze":     atr / max(bu - bl, 1e-6),
        "ema20":          ema20,
        "vol_ratio":      round(vol_ratio, 2),
        "candles":        candles,
    }


def _zero_metrics() -> PerformanceMetrics:
    return PerformanceMetrics(
        total_trades=0, win_trades=0, loss_trades=0, win_rate=0.0,
        total_pnl=0.0, avg_win=0.0, avg_loss=0.0, profit_factor=0.0,
        expectancy=0.0, max_drawdown=0.0, sharpe_ratio=None,
        best_trade=0.0, worst_trade=0.0, avg_holding_days=None,
    )


# ── Output formatting ─────────────────────────────────────────────────────────

def _print_results(results: list[IntradayBacktestResult]) -> None:
    if not results:
        print("No results generated.")
        return

    r0 = results[0]
    sep = "=" * 78
    print(f"\n{sep}")
    print(
        f"  INTRADAY BACKTEST  |  {r0.interval} bars  |  "
        f"{r0.date_range}  |  {r0.symbols_tested} symbols"
    )
    print(sep)
    header = (
        f"  {'Strategy':<20}  {'Trades':>6}  {'WR%':>6}  "
        f"{'PF':>6}  {'Sharpe':>7}  {'MDD%':>6}  {'Avg%':>7}"
    )
    print(header)
    dash = "  " + "-" * 72

    individual = [r for r in results if "Consensus" not in r.strategy_name]
    consensus  = [r for r in results if "Consensus"     in r.strategy_name]

    print(dash)
    for r in sorted(individual, key=lambda x: x.metrics.sharpe_ratio or -99, reverse=True):
        _print_row(r)

    if consensus:
        print(dash)
        for r in consensus:
            _print_row(r)

    print(f"{sep}\n")


def _intraday_stats(trades: list[IntradayTrade]) -> dict:
    """High-precision stats computed directly from trades (bypasses rounding in metrics.py)."""
    if not trades:
        return {}
    pnls   = [t.pnl_pct for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n      = len(pnls)
    wr     = len(wins) / n
    avg_w  = sum(wins)   / len(wins)   if wins   else 0.0
    avg_l  = abs(sum(losses) / len(losses)) if losses else 0.0
    pf     = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
    exp    = wr * avg_w - (1 - wr) * avg_l
    # Max drawdown as % of cumulative capital (capped naturally)
    peak = cum = mdd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = (peak - cum) if peak > 0 else max(0.0, -cum)
        if dd > mdd:
            mdd = dd
    return {"wr": wr, "pf": pf, "exp": exp, "mdd": mdd}


def _print_row(r: IntradayBacktestResult) -> None:
    m  = r.metrics
    sh = f"{m.sharpe_ratio:>7.2f}" if m.sharpe_ratio is not None else "    N/A"
    if r.total_trades == 0:
        body = f"{'(no signals)':>58}"
    else:
        s = _intraday_stats(r.trades)
        body = (
            f"{r.total_trades:>6}  "
            f"{s['wr']*100:>6.1f}  "
            f"{s['pf']:>6.2f}  "
            f"{sh}  "
            f"{s['mdd']*100:>6.1f}  "
            f"{s['exp']*100:>+8.3f}"
        )
    print(f"  {r.strategy_name:<20}  {body}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Intraday backtester — high-beta NSE stocks"
    )
    parser.add_argument(
        "--week", action="store_true",
        help="Test the current trading week (Monday → today)",
    )
    parser.add_argument("--start",    type=str, default=None, help="Start date ISO e.g. 2026-05-19")
    parser.add_argument("--end",      type=str, default=None, help="End date ISO e.g. 2026-05-23")
    parser.add_argument(
        "--interval", type=str, default="5m",
        choices=["1m", "2m", "5m", "15m", "30m", "1h"],
        help="Bar interval (default: 5m)",
    )
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated NSE symbols (default: 20 high-beta stocks)",
    )
    parser.add_argument(
        "--strategy", type=str, default=None,
        help="Single strategy key: vwap | macdrsí | meanreversion | supertrend",
    )
    parser.add_argument(
        "--no-consensus", action="store_true",
        help="Skip consensus engine pass",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Date range
    if args.week:
        today      = date.today()
        start_date = (today - timedelta(days=today.weekday())).isoformat()
        end_date   = today.isoformat()
    elif args.start and args.end:
        start_date = args.start
        end_date   = args.end
    else:
        parser.print_help()
        return

    # Symbols
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols else HIGH_BETA_STOCKS
    )

    # Strategies
    if args.strategy:
        key   = args.strategy.lower().replace(" ", "").replace("+", "").replace("-", "")
        strat = STRATEGY_MAP.get(key)
        if strat is None:
            print(f"Unknown strategy '{args.strategy}'. Available: {list(STRATEGY_MAP.keys())}")
            return
        strategies = [strat]
    else:
        strategies = INTRADAY_STRATEGIES

    print(f"\nBacktesting {len(strategies)} intraday strateg{'y' if len(strategies)==1 else 'ies'} "
          f"on {len(symbols)} symbols")
    print(f"Range   : {start_date} to {end_date}")
    print(f"Interval: {args.interval}")
    print(f"Symbols : {', '.join(symbols[:10])}{'  ...' if len(symbols) > 10 else ''}\n")

    backtester = IntradayBacktester()
    results = backtester.run(
        strategies=strategies,
        symbols=symbols,
        interval=args.interval,
        start_date=start_date,
        end_date=end_date,
        run_consensus=not args.no_consensus,
    )

    _print_results(results)

    # Save JSON results
    out_dir    = Path("data/backtest_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    date_range = f"{start_date}_to_{end_date}"

    for r in results:
        safe = (
            r.strategy_name
            .replace("/", "_").replace("(", "").replace(")", "")
            .replace(">=", "gte").replace("≥", "gte")
            .replace(">", "gt").replace("<", "lt")
            .replace(" ", "_").replace("+", "plus")
        )
        path = out_dir / f"intraday_{safe}_{r.interval}_{date_range}.json"
        out  = {
            "strategy":       r.strategy_name,
            "interval":       r.interval,
            "date_range":     r.date_range,
            "symbols_tested": r.symbols_tested,
            "total_trades":   r.total_trades,
            "win_rate":       r.metrics.win_rate,
            "profit_factor":  r.metrics.profit_factor,
            "sharpe_ratio":   r.metrics.sharpe_ratio,
            "max_drawdown":   r.metrics.max_drawdown,
            "expectancy_pct": round(r.metrics.expectancy * 100, 4),
            "avg_win_pct":    round(r.metrics.avg_win  * 100, 4),
            "avg_loss_pct":   round(r.metrics.avg_loss * 100, 4),
            "trades": [
                {
                    "symbol":       t.symbol,
                    "date":         t.date,
                    "entry_time":   t.entry_time,
                    "exit_time":    t.exit_time,
                    "entry_price":  t.entry_price,
                    "exit_price":   t.exit_price,
                    "pnl_pct":      round(t.pnl_pct * 100, 4),
                    "pnl_abs":      t.pnl_abs,
                    "holding_bars": t.holding_bars,
                    "exit_reason":  t.exit_reason,
                }
                for t in r.trades
            ],
        }
        path.write_text(json.dumps(out, indent=2))
        print(f"Saved: {path}")


if __name__ == "__main__":
    main()
