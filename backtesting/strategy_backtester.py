"""
Per-strategy historical backtester.

Tests each strategy against 1-year daily OHLCV for Nifty 50 symbols.
Uses the existing metrics.py for performance statistics.

Usage:
    python -m backtesting.strategy_backtester --strategy momentum --period 1y
    python -m backtesting.strategy_backtester --all --period 1y
    python -m backtesting.strategy_backtester --all --symbols RELIANCE,TCS,HDFCBANK
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yfinance as yf
import pandas as pd

from backtesting.metrics import compute_metrics, PerformanceMetrics
from strategies.base import BaseStrategy, StrategySignal
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.macd_rsi_confluence import MacdRsiConfluenceStrategy
from strategies.supertrend import SupertrendStrategy
from strategies.vwap_reversion import VwapReversionStrategy
from strategies.bollinger_squeeze import BollingerSqueezeStrategy

logger = logging.getLogger(__name__)

ALL_STRATEGIES: list[BaseStrategy] = [
    MomentumStrategy(),
    MeanReversionStrategy(),
    MacdRsiConfluenceStrategy(),
    SupertrendStrategy(),
    VwapReversionStrategy(),
    BollingerSqueezeStrategy(),
]

STRATEGY_MAP: dict[str, BaseStrategy] = {s.name.lower(): s for s in ALL_STRATEGIES}


@dataclass
class BacktestTrade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float       # percentage P&L
    pnl_abs: float       # absolute P&L per share
    holding_days: float
    action: str


@dataclass
class BacktestResult:
    strategy_name: str
    period: str
    symbols_tested: int
    total_trades: int
    metrics: PerformanceMetrics
    trades: list[BacktestTrade] = field(default_factory=list)

    def summary(self) -> str:
        m = self.metrics
        lines = [
            f"Strategy: {self.strategy_name}  |  Period: {self.period}  |  Symbols: {self.symbols_tested}",
            f"  Trades: {self.total_trades}  WR: {m.win_rate*100:.1f}%  PF: {m.profit_factor:.2f}  "
            f"Sharpe: {m.sharpe_ratio:.2f}  MDD: {m.max_drawdown*100:.1f}%",
            f"  Avg P&L/trade: {m.expectancy*100:.2f}%  "
            f"Best: +{m.best_trade*100:.1f}%  Worst: {m.worst_trade*100:.1f}%",
        ]
        return "\n".join(lines)


class StrategyBacktester:
    """
    Backtests a single strategy on historical daily OHLCV data.

    Simulates:
    - Entry on signal day close
    - Exit at target, stop-loss, or max_hold_days (whichever comes first)
    - One position per symbol at a time
    """

    MAX_HOLD_DAYS = 20     # max holding period if no target/SL hit
    LOOKBACK_BARS = 260    # bars of history to build indicators before testing

    def run(
        self,
        strategy: BaseStrategy,
        symbols: list[str],
        period: str = "1y",
    ) -> BacktestResult:
        all_pnls: list[float] = []
        all_holding_days: list[float] = []
        all_trades: list[BacktestTrade] = []

        for symbol in symbols:
            try:
                trades = self._backtest_symbol(strategy, symbol, period)
                for t in trades:
                    all_pnls.append(t.pnl_pct)
                    all_holding_days.append(t.holding_days)
                all_trades.extend(trades)
            except Exception as exc:
                logger.debug("Backtest error %s/%s: %s", strategy.name, symbol, exc)

        if not all_pnls:
            # No trades generated — return zero-fill metrics
            metrics = PerformanceMetrics(
                total_trades=0, win_trades=0, loss_trades=0, win_rate=0.0,
                total_pnl=0.0, avg_win=0.0, avg_loss=0.0,
                profit_factor=0.0, expectancy=0.0, max_drawdown=0.0,
                sharpe_ratio=None, best_trade=0.0, worst_trade=0.0,
                avg_holding_days=None,
            )
        else:
            metrics = compute_metrics(all_pnls, all_holding_days)

        return BacktestResult(
            strategy_name=strategy.name,
            period=period,
            symbols_tested=len(symbols),
            total_trades=len(all_pnls),
            metrics=metrics,
            trades=all_trades,
        )

    def _backtest_symbol(
        self,
        strategy: BaseStrategy,
        symbol: str,
        period: str,
    ) -> list[BacktestTrade]:
        """Run strategy on one symbol. Returns list of completed trades."""
        df = self._fetch_ohlcv(symbol, period)
        if df is None or len(df) < self.LOOKBACK_BARS + 5:
            return []

        trades: list[BacktestTrade] = []
        in_trade = False
        entry_price = 0.0
        entry_date = ""
        stop_loss = 0.0
        target = 0.0
        entry_idx = 0

        for i in range(self.LOOKBACK_BARS, len(df)):
            row = df.iloc[i]
            close = float(row["Close"])
            date_str = str(df.index[i])[:10]

            if in_trade:
                # Check exit conditions
                high  = float(row["High"])
                low   = float(row["Low"])
                days_held = i - entry_idx

                hit_target = high >= target
                hit_stop   = low <= stop_loss
                max_hold   = days_held >= self.MAX_HOLD_DAYS

                if hit_target or hit_stop or max_hold:
                    exit_price = target if hit_target else (stop_loss if hit_stop else close)
                    pnl_pct    = (exit_price - entry_price) / entry_price
                    trades.append(BacktestTrade(
                        symbol=symbol,
                        entry_date=entry_date,
                        exit_date=date_str,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        pnl_pct=pnl_pct,
                        pnl_abs=exit_price - entry_price,
                        holding_days=float(days_held),
                        action="BUY",
                    ))
                    in_trade = False
            else:
                # Build ohlcv dict from historical window
                window = df.iloc[max(0, i - self.LOOKBACK_BARS): i + 1]
                ohlcv = self._build_ohlcv_dict(window, symbol)
                if not ohlcv:
                    continue

                signal: StrategySignal = strategy.generate_signal(ohlcv)

                if signal.action == "BUY" and signal.confidence >= 0.45:
                    in_trade    = True
                    entry_price = close
                    entry_date  = date_str
                    entry_idx   = i
                    stop_loss   = signal.stop_loss
                    target      = signal.target

        return trades

    def _fetch_ohlcv(self, symbol: str, period: str) -> pd.DataFrame | None:
        # Try cached file first
        cache_path = Path(f"data/backtest_cache/{symbol}_{period}.parquet")
        if cache_path.exists():
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            df = ticker.history(period=period, interval="1d", auto_adjust=True)
            if df.empty:
                return None
            # Cache for re-use within this session
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                df.to_parquet(cache_path)
            except Exception:
                pass
            return df
        except Exception as exc:
            logger.debug("yfinance fetch error %s: %s", symbol, exc)
            return None

    @staticmethod
    def _build_ohlcv_dict(df: pd.DataFrame, symbol: str) -> dict:
        """Convert a DataFrame window into the OHLCV dict format used by strategies."""
        if df.empty or len(df) < 30:
            return {}

        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]
        vol   = df["Volume"]

        # RSI-14
        delta = close.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        ag    = gain.ewm(com=13, min_periods=14).mean()
        al    = loss.ewm(com=13, min_periods=14).mean()
        rs    = ag / al.replace(0, float("nan"))
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1]) if not rs.empty else 50.0

        # MACD
        fast_ema = close.ewm(span=12, adjust=False).mean()
        slow_ema = close.ewm(span=26, adjust=False).mean()
        macd_line = fast_ema - slow_ema
        sig_line  = macd_line.ewm(span=9, adjust=False).mean()
        hist      = macd_line - sig_line
        prev_hist = float(hist.iloc[-2]) if len(hist) > 1 else 0
        last_hist = float(hist.iloc[-1])
        macd_cross = (last_hist > 0) and (prev_hist <= 0)

        # EMA-200
        ema200 = close.ewm(span=200, adjust=False).mean()
        last_close = float(close.iloc[-1])
        above_ema200 = last_close > float(ema200.iloc[-1])

        # ATR-14
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.ewm(com=13, min_periods=14).mean().iloc[-1])

        # Bollinger Bands
        bb_mid   = close.rolling(20).mean()
        bb_std   = close.rolling(20).std(ddof=0)
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bu = float(bb_upper.iloc[-1])
        bl = float(bb_lower.iloc[-1])
        bm = float(bb_mid.iloc[-1])
        bb_pct = (last_close - bl) / max(bu - bl, 0.0001)

        # Volume ratio
        vol_ratio = float(vol.iloc[-1] / vol.rolling(20).mean().iloc[-1]) if vol.rolling(20).mean().iloc[-1] > 0 else 1.0

        # Candle list
        candles = [
            {"h": float(r["High"]), "l": float(r["Low"]), "c": float(r["Close"]),
             "v": int(r["Volume"]), "o": float(r["Open"])}
            for _, r in df.tail(60).iterrows()
        ]

        # EMA-20
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])

        return {
            "symbol":       symbol,
            "last_close":   last_close,
            "rsi":          rsi,
            "macd_hist":    last_hist,
            "macd_crossover": macd_cross,
            "above_ema200": above_ema200,
            "atr":          atr,
            "bb_upper":     bu,
            "bb_mid":       bm,
            "bb_lower":     bl,
            "bb_pct":       bb_pct,
            "bb_squeeze":   atr / max(bu - bl, 0.0001),
            "ema20":        ema20,
            "vol_ratio":    round(vol_ratio, 2),
            "candles":      candles,
        }


# ── CLI entry point ────────────────────────────────────────────────────────────

def _print_comparison_table(results: list[BacktestResult]) -> None:
    header = f"{'Strategy':<18} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Sharpe':>7} {'MDD%':>6} {'Avg%':>7}"
    print()
    print(header)
    print("-" * len(header))
    for r in sorted(results, key=lambda x: x.metrics.sharpe_ratio or 0.0, reverse=True):
        m = r.metrics
        sharpe_str = f"{m.sharpe_ratio:>7.2f}" if m.sharpe_ratio is not None else "    N/A"
        print(
            f"{r.strategy_name:<18} {r.total_trades:>7} "
            f"{m.win_rate*100:>6.1f} {m.profit_factor:>6.2f} "
            f"{sharpe_str} {m.max_drawdown*100:>6.1f} "
            f"{m.expectancy*100:>7.2f}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy backtester")
    parser.add_argument("--strategy", type=str, help="Strategy name (momentum, meanreversion, etc.)")
    parser.add_argument("--all",      action="store_true", help="Run all strategies")
    parser.add_argument("--period",   type=str, default="1y", help="yfinance period (e.g. 6mo, 1y, 2y)")
    parser.add_argument("--symbols",  type=str, help="Comma-separated symbols (default: Nifty 50)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        from config.instruments import NIFTY_50
        symbols = [inst.symbol for inst in NIFTY_50]

    backtester = StrategyBacktester()

    if args.all:
        strategies = ALL_STRATEGIES
    elif args.strategy:
        key = args.strategy.lower().replace(" ", "").replace("+", "").replace("-", "")
        strat = STRATEGY_MAP.get(key)
        if strat is None:
            print(f"Unknown strategy '{args.strategy}'. Available: {list(STRATEGY_MAP.keys())}")
            return
        strategies = [strat]
    else:
        parser.print_help()
        return

    results = []
    for strat in strategies:
        print(f"Backtesting {strat.name} on {len(symbols)} symbols ({args.period})…")
        result = backtester.run(strat, symbols, args.period)
        results.append(result)
        print(result.summary())

    if len(results) > 1:
        _print_comparison_table(results)

    # Save results to JSON
    out_dir = Path("data/backtest_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        out = {
            "strategy": r.strategy_name,
            "period": r.period,
            "symbols_tested": r.symbols_tested,
            "total_trades": r.total_trades,
            "win_rate":      r.metrics.win_rate,
            "profit_factor": r.metrics.profit_factor,
            "sharpe_ratio":  r.metrics.sharpe_ratio,
            "max_drawdown":  r.metrics.max_drawdown,
            "expectancy":    r.metrics.expectancy,
        }
        path = out_dir / f"{r.strategy_name}_{args.period}.json"
        path.write_text(json.dumps(out, indent=2))
        print(f"Results saved → {path}")


if __name__ == "__main__":
    main()
