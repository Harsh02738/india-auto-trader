"""
Live-feel capital simulation with Rs 1,00,000 starting capital.

Fetches fresh 5m intraday data (no pre-computed JSON), runs all 4 strategies
in priority order (MeanReversion > MACD+RSI > Supertrend > VWAP), applies
real position sizing from the live formula, enforces concurrent position
limits, and tracks circuit breakers exactly as the live system would.

Usage:
    python -m backtesting.capital_sim --capital 100000 --week --interval 5m
    python -m backtesting.capital_sim --capital 100000 --start 2026-05-12 --end 2026-05-23
    python -m backtesting.capital_sim --capital 100000 --symbols INDUSINDBK,TATASTEEL --week
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Reuse data + indicator helpers from intraday_backtester (no duplication)
from backtesting.intraday_backtester import (
    HIGH_BETA_STOCKS,
    _LOOKBACK_CALENDAR_DAYS,
    _build_ohlcv_dict,
    _daily_ema200,
    _fetch_daily,
    _past_cutoff,
    _to_ist,
    fetch_intraday_chunked,
)
from strategies.base import BaseStrategy, StrategySignal
from strategies.macd_rsi_confluence import MacdRsiConfluenceStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.supertrend import SupertrendStrategy
from strategies.vwap_reversion import VwapReversionStrategy

logger = logging.getLogger(__name__)

# ── Capital / risk parameters ─────────────────────────────────────────────────

DEFAULT_CAPITAL    = 1_00_000   # Rs
MAX_RISK_PCT       = 0.02       # 2% max risk per trade
MAX_NOTIONAL_PCT   = 0.05       # 5% notional cap per position
MAX_OPEN           = 3          # max concurrent open positions
MIN_CONFIDENCE      = 0.55       # minimum strategy confidence to enter
MIN_CONFIDENCE_LATE = 0.55       # stricter after 13:00 (less time to target)
LATE_ENTRY_HOUR     = 13         # threshold for late-session filter
MIN_RR              = 2.5        # minimum risk:reward ratio (raised from 2.0)
MIN_VOL_RATIO       = 1.2        # require 20% above avg volume for conviction
SKIP_FIRST_BARS     = 15         # skip first 75 min (9:15-10:30 AM open volatility)
LAST_ENTRY_HOUR     = 14         # no new entries at or after 14:30
LAST_ENTRY_MIN      = 30
CLOSE_HOUR          = 15         # force-close all MIS at 15:15
CLOSE_MIN           = 15
STALE_BARS          = 30         # bars before checking for stale trade (150 min at 5m)
STALE_MIN_PCT       = 0.002      # 0.2% minimum price progress to stay in trade
SYMBOL_COOLDOWN_MINS = 20        # block re-entry for 20 min after a losing exit
DAILY_UNIVERSE_SIZE  = 15        # top N stocks selected each morning by momentum score
OR_MIN_SCORE         = 0.40      # min opening-range score (10:30 AM screen) to allow entry

# Circuit breaker thresholds (same as config/settings.py)
CB_DAILY_LOSS_PCT  = 0.03       # -3% daily loss -> HALT
CB_CONSEC_LOSSES   = 5          # 5 consecutive losses -> HALT
CB_DRAWDOWN_PCT    = 0.15       # -15% peak-to-trough -> HALT

# ── Broker/exchange charges (Kotak Intraday, NSE Equity) ──────────────────────
_BROK_CAP   = 10.0          # Rs per order (brokerage ceiling)
_BROK_PCT   = 0.0005        # 0.05% per order, whichever is lower
_TXN_PCT    = 0.000030699   # NSE transaction charge each side
_STT_PCT    = 0.00025       # 0.025% sell-side only (intraday MIS)
_STAMP_PCT  = 0.00003       # 0.003% buy-side only


def _calc_trade_costs(buy_notional: float, sell_notional: float) -> float:
    """Round-trip cost: brokerage + NSE txn + STT (sell) + stamp (buy)."""
    brok  = min(_BROK_CAP, _BROK_PCT * buy_notional) \
          + min(_BROK_CAP, _BROK_PCT * sell_notional)
    txn   = _TXN_PCT * (buy_notional + sell_notional)
    stt   = _STT_PCT * sell_notional
    stamp = _STAMP_PCT * buy_notional
    return round(brok + txn + stt + stamp, 2)

# ── Sector mapping for diversification cap ───────────────────────────────────
_SYMBOL_SECTOR: dict[str, str] = {
    "TATAMOTORS": "Auto",    "M&M": "Auto",         "EICHERMOT": "Auto",
    "TVSMOTOR":   "Auto",    "HEROMOTOCO": "Auto",   "MARUTI":    "Auto",
    "INDUSINDBK": "Banking", "IDFCFIRSTB": "Banking","BANKBARODA":"Banking",
    "FEDERALBNK": "Banking", "HDFCBANK":   "Banking","ICICIBANK": "Banking",
    "AXISBANK":   "Banking", "SBIN":       "Banking",
    "TATASTEEL":  "Metals",  "JSWSTEEL":   "Metals",
    "HINDALCO":   "Metals",  "VEDL":       "Metals",
    "BAJFINANCE": "NBFC",    "SBICARD":    "NBFC",   "BAJAJFINSV":"NBFC",
    "CHOLAFIN":   "NBFC",
    "ADANIENT":   "Conglomerate", "ADANIPORTS": "Infrastructure",
    "BEL":        "Defence", "HAL":        "Defence",
    "RELIANCE":   "Energy",
    "HCLTECH":    "IT",
    "LT":         "CapGoods",
}

# Strategy priority — higher-alpha strategies evaluated first per symbol per bar
STRATEGY_PRIORITY: list[BaseStrategy] = [
    MeanReversionStrategy(),       # Sharpe 6.49, WR 65.8%  — best
    MacdRsiConfluenceStrategy(),   # Sharpe 2.81, WR 56.5%  — solid
    SupertrendStrategy(),          # Sharpe 2.27, WR 50.9%  — high freq
    VwapReversionStrategy(),       # fires only above EMA-200
]

# Sharpe-based quality weights — all strategies compete, best score wins
_STRATEGY_SHARPE_WEIGHT: dict[str, float] = {
    "MeanReversion": 1.00,
    "MACD+RSI":      0.75,
    "Supertrend":    0.60,
    "VwapReversion": 0.55,
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class _Position:
    symbol:              str
    strategy:            str
    entry_price:         float
    stop_loss:           float
    target:              float
    qty:                 int
    confidence:          float
    rr:                  float
    entry_time_str:      str     # "HH:MM"
    notional:            float   # qty * entry_price
    risk_rs:             float   # qty * (entry - stop_loss)
    bars_held:           int = 0
    breakeven_activated: bool = False


@dataclass
class SimTrade:
    symbol:      str
    strategy:    str
    date:        str
    entry_time:  str
    exit_time:   str
    entry_price: float
    exit_price:  float
    qty:         int
    pnl_rs:      float      # net after transaction costs
    pnl_pct:     float
    notional:    float
    exit_reason: str        # "target" | "stop_loss" | "session_end" | "stale_exit"
    costs_rs:    float = 0.0
    gross_rs:    float = 0.0


# ── Capital simulator ─────────────────────────────────────────────────────────

class CapitalSimulator:
    """
    Simulates multi-symbol intraday trading on 5m (or 1m) bar data with
    real capital tracking, position sizing, and circuit breakers.
    """

    def __init__(self, capital: float = DEFAULT_CAPITAL):
        self.initial_capital = capital
        self.equity          = capital
        self.peak_equity     = capital

        self.open_positions: dict[str, _Position] = {}   # symbol -> Position
        self.all_trades:     list[SimTrade]        = []
        self.daily_summary:  list[dict]            = []

        self.consecutive_losses = 0
        self.cb_state           = "SAFE"    # SAFE | WARNING | TRIPPED
        self.cb_reason          = ""

        self._day_pnl_rs  = 0.0
        self._day_trades: list[SimTrade] = []
        self._symbol_block_until: dict[str, "pd.Timestamp"] = {}  # cooldown after loss

    # ── Public entry point ────────────────────────────────────────────────────

    def run(
        self,
        symbols:    list[str],
        interval:   str,
        start_date: str,
        end_date:   str,
    ) -> None:
        _print_banner(self.initial_capital, interval, start_date, end_date)

        lookback_days  = _LOOKBACK_CALENDAR_DAYS.get(interval, 20)
        lookback_start = (
            date.fromisoformat(start_date) - timedelta(days=lookback_days)
        ).isoformat()

        # Fetch all data upfront (cached to data/backtest_cache/ as Parquet)
        sym_data: dict[str, tuple] = {}
        for sym in symbols:
            try:
                df = fetch_intraday_chunked(sym, interval, lookback_start, end_date)
                if df is None or df.empty:
                    logger.info("No intraday data for %s — skipping", sym)
                    continue
                df = _to_ist(df)
                sym_data[sym] = (df, _fetch_daily(sym))
            except Exception as exc:
                logger.debug("Fetch error %s: %s", sym, exc)

        if not sym_data:
            print("No data fetched — check symbol names and date range.")
            return

        start_d = date.fromisoformat(start_date)
        end_d   = date.fromisoformat(end_date)

        trading_days = sorted({
            ts.date()
            for df, _ in sym_data.values()
            for ts in df.index
            if start_d <= ts.date() <= end_d
        })

        for session_date in trading_days:
            self._run_day(session_date, sym_data)

        self._print_final_report()
        self._save_results(interval, start_date, end_date)

    # ── Per-day simulation ────────────────────────────────────────────────────

    def _rank_symbols_for_day(self, session_date, sym_data: dict) -> list[str]:
        """Score each symbol by prior-day momentum + volume ratio + ATR%.
        Returns top DAILY_UNIVERSE_SIZE symbols to trade today.
        """
        scores: dict[str, float] = {}
        for sym, (full_df, daily_df) in sym_data.items():
            try:
                prev = daily_df[daily_df.index.date < session_date]
                if len(prev) < 21:
                    scores[sym] = 0.0
                    continue
                close_s = prev["Close"]
                vol_s   = prev["Volume"]
                high_s  = prev["High"]
                low_s   = prev["Low"]

                avg_vol  = float(vol_s.tail(20).mean())
                yday_cl  = float(close_s.iloc[-1])
                prev2_cl = float(close_s.iloc[-2])
                yday_vol = float(vol_s.iloc[-1])

                momentum  = (yday_cl - prev2_cl) / prev2_cl
                vol_ratio = yday_vol / avg_vol if avg_vol > 0 else 1.0
                atr14     = float((high_s - low_s).tail(14).mean())
                atr_pct   = atr14 / yday_cl * 100

                # Candle quality: closed in top 50% of yesterday's range → bullish
                yday_open  = float(prev["Open"].iloc[-1])
                yday_high  = float(high_s.iloc[-1])
                yday_low   = float(low_s.iloc[-1])
                yday_range = yday_high - yday_low
                candle_pos = (yday_cl - yday_low) / yday_range if yday_range > 0 else 0.5

                # EMA-20 trend: price above short-term trend → momentum intact
                ema20       = float(close_s.ewm(span=20, adjust=False).mean().iloc[-1])
                above_ema20 = 1.0 if yday_cl > ema20 else 0.0

                # RSI-14 zone: 40-65 = momentum without exhaustion
                delta     = close_s.diff()
                gain      = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
                loss      = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
                rsi       = 100 - 100 / (1 + gain.iloc[-1] / max(loss.iloc[-1], 1e-9))
                rsi_score = 1.0 if 40 <= rsi <= 65 else (0.5 if 35 <= rsi <= 72 else 0.0)

                scores[sym] = (
                    max(0.0, momentum)            * 3.0   # prior day % gain
                    + max(0.0, vol_ratio - 1.0)  * 2.0   # above-avg volume
                    + max(0.0, min(atr_pct, 4.0)) * 0.5  # ATR% sweet spot
                    + max(0.0, candle_pos - 0.5) * 1.5   # closed in top half of range
                    + above_ema20                 * 1.0   # above EMA-20
                    + rsi_score                   * 1.0   # RSI in momentum zone
                )
            except Exception:
                scores[sym] = 0.0

        ranked = sorted(scores, key=scores.get, reverse=True)
        return ranked[:DAILY_UNIVERSE_SIZE]

    def _screen_opening_range(self, sym_sessions: dict) -> list[str]:
        """Score each symbol by first 75 min (SKIP_FIRST_BARS) of today's trading.
        Returns symbols whose opening action is bullish enough to trade.
        Called exactly once per session at the first tradeable bar (10:30 AM).
        """
        approved: list[tuple] = []
        for sym, (combined, wlen, day_df, above, ts_to_idx) in sym_sessions.items():
            try:
                or_bars = day_df.iloc[:SKIP_FIRST_BARS]
                if len(or_bars) < 5:
                    continue
                prev_close = float(combined.iloc[wlen - 1]["Close"])
                or_open    = float(or_bars.iloc[0]["Open"])
                or_close   = float(or_bars.iloc[-1]["Close"])

                score = 0.0

                # 1. Gap up from prior close (reward gaps 0-3%, cap at 3%)
                gap_pct = (or_open - prev_close) / prev_close
                if 0 < gap_pct < 0.03:
                    score += gap_pct * 10          # max +0.30
                elif gap_pct >= 0.03:
                    score += 0.20                  # capped (large gaps often fade)

                # 2. Opening range trending up (bar 0 to bar 14)
                or_return = (or_close - or_open) / or_open
                if or_return > 0:
                    score += min(or_return * 20, 0.40)  # max +0.40 for 2% gain

                # 3. Holding above prior close at 10:30 AM
                if or_close > prev_close:
                    score += 0.30

                # 4. Volume surge vs warmup average
                or_vol      = float(or_bars["Volume"].sum())
                avg_bar_vol = float(combined.iloc[:wlen]["Volume"].mean())
                expected_or_vol = avg_bar_vol * SKIP_FIRST_BARS
                or_vol_ratio = or_vol / expected_or_vol if expected_or_vol > 0 else 1.0
                if or_vol_ratio >= 1.5:
                    score += 0.30
                elif or_vol_ratio >= 1.0:
                    score += 0.15

                # 5. Bullish bar ratio (close >= open) in opening range
                bull_count = sum(
                    1 for _, row in or_bars.iterrows()
                    if float(row["Close"]) >= float(row["Open"])
                )
                score += (bull_count / len(or_bars)) * 0.30  # max +0.30

                # 6. Daily trend context
                if above:
                    score += 0.20

                if score >= OR_MIN_SCORE:
                    approved.append((sym, score))
            except Exception:
                continue

        approved.sort(key=lambda x: x[1], reverse=True)
        return [sym for sym, _ in approved]

    def _run_day(self, session_date, sym_data: dict) -> None:
        self._day_pnl_rs = 0.0
        self._day_trades = []
        self._symbol_block_until.clear()  # reset cooldowns at session start

        # Reset day-specific CBs (daily_loss + consecutive_losses); drawdown persists
        if self.cb_state == "TRIPPED" and "drawdown" not in self.cb_reason:
            self.cb_state  = "SAFE"
            self.cb_reason = ""
        self.consecutive_losses = 0  # loss streak always resets for new session

        # Morning scoring: select top DAILY_UNIVERSE_SIZE stocks "in play" for today
        day_universe = set(self._rank_symbols_for_day(session_date, sym_data))

        # Build per-symbol session structures for this day (only ranked universe)
        sym_sessions: dict[str, tuple] = {}
        for sym, (full_df, daily_df) in sym_data.items():
            if sym not in day_universe:
                continue
            day_df = full_df[full_df.index.date == session_date]
            try:
                day_df = day_df.between_time("09:15", "15:30")
            except Exception:
                pass
            if len(day_df) < SKIP_FIRST_BARS + 2:
                continue
            pre_df   = full_df[full_df.index.date < session_date]
            warmup   = pre_df.iloc[-200:]
            above    = _daily_ema200(daily_df, session_date)
            combined = pd.concat([warmup, day_df])   # pre-concat once per session
            wlen     = len(warmup)
            ts_to_idx = {ts: i for i, ts in enumerate(day_df.index)}
            sym_sessions[sym] = (combined, wlen, day_df, above, ts_to_idx)

        if not sym_sessions:
            return

        # Sorted union of all bar timestamps for this day
        all_ts = sorted({
            ts
            for _, (_, _, day_df, _, _) in sym_sessions.items()
            for ts in day_df.index
        })

        _print_day_header(session_date)

        or_symbols: set[str] | None = None  # computed once at first entry bar (10:30 AM)

        for ts in all_ts:
            bar_time         = ts.time()
            is_force_close   = _past_cutoff(bar_time, CLOSE_HOUR, CLOSE_MIN)
            is_past_entry    = _past_cutoff(bar_time, LAST_ENTRY_HOUR, LAST_ENTRY_MIN)

            # ── Phase 1: Exit all open positions that hit SL / target / close ──
            for sym in list(self.open_positions.keys()):
                if sym not in sym_sessions:
                    continue
                _, _, day_df, _, _ = sym_sessions[sym]
                if ts not in day_df.index:
                    continue

                pos   = self.open_positions[sym]
                high  = float(day_df.loc[ts, "High"])
                low   = float(day_df.loc[ts, "Low"])
                close = float(day_df.loc[ts, "Close"])

                pos.bars_held += 1

                # Breakeven stop: move SL to entry when price reaches 50% of the way to target
                if not pos.breakeven_activated:
                    halfway = pos.entry_price + 0.5 * (pos.target - pos.entry_price)
                    if high >= halfway:
                        pos.breakeven_activated = True
                        pos.stop_loss = pos.entry_price

                if is_force_close:
                    self._close(sym, pos, close, ts, "session_end")
                elif low <= pos.stop_loss:
                    self._close(sym, pos, pos.stop_loss, ts, "stop_loss")
                elif high >= pos.target:
                    self._close(sym, pos, pos.target, ts, "target")
                elif (pos.bars_held >= STALE_BARS
                        and not pos.breakeven_activated
                        and (close - pos.entry_price) / pos.entry_price < STALE_MIN_PCT):
                    # Stale trade: no progress after 150 min — exit to free capital
                    self._close(sym, pos, close, ts, "stale_exit")

            # ── Phase 2: New entries ──────────────────────────────────────────
            if is_force_close or is_past_entry:
                continue
            if self.cb_state == "TRIPPED":
                continue
            if len(self.open_positions) >= MAX_OPEN:
                continue

            # Late-session tightening: require higher confidence after 13:00
            min_conf = MIN_CONFIDENCE_LATE if bar_time.hour >= LATE_ENTRY_HOUR else MIN_CONFIDENCE

            # One-time opening range screen: run lazily at first entry bar (10:30 AM)
            if or_symbols is None:
                or_symbols = set(self._screen_opening_range(sym_sessions))

            # All strategies compete: sym -> (score, sig, strat_name, qty)
            candidates: dict[str, tuple] = {}

            for sym, (combined, wlen, day_df, above, ts_to_idx) in sym_sessions.items():
                if sym in self.open_positions:
                    continue
                # Symbol cooldown: block re-entry for 20 min after a loss
                if sym in self._symbol_block_until and ts < self._symbol_block_until[sym]:
                    continue
                # Sector cap: max 1 open position per sector
                sym_sector   = _SYMBOL_SECTOR.get(sym, "")
                open_sectors = {_SYMBOL_SECTOR.get(s, "") for s in self.open_positions}
                if sym_sector and sym_sector in open_sectors:
                    continue
                # Opening range gate: only trade stocks with bullish 10:30 AM behavior
                if sym not in or_symbols:
                    continue
                if ts not in ts_to_idx:
                    continue
                bar_idx = ts_to_idx[ts]
                if bar_idx < SKIP_FIRST_BARS:
                    continue  # still in open-volatility window

                window     = combined.iloc[:wlen + bar_idx + 1]
                day_so_far = combined.iloc[wlen:wlen + bar_idx + 1]
                ohlcv = _build_ohlcv_dict(window, day_so_far, sym, above)
                if not ohlcv:
                    continue

                # Volume gate: require active buying before entering
                if ohlcv.get("vol_ratio", 1.0) < MIN_VOL_RATIO:
                    continue

                # Evaluate ALL strategies — no break; best quality score wins
                for strategy in STRATEGY_PRIORITY:
                    try:
                        sig: StrategySignal = strategy.generate_signal(ohlcv)
                    except Exception:
                        continue
                    if (sig.action != "BUY"
                            or sig.confidence < min_conf
                            or sig.risk_reward < MIN_RR):
                        continue
                    qty = _size_position(self.equity, sig.entry, sig.stop_loss)
                    if qty == 0:
                        continue
                    exp_gross = qty * (sig.target - sig.entry)
                    exp_costs = _calc_trade_costs(qty * sig.entry, qty * sig.target)
                    if exp_gross < 2.0 * exp_costs:
                        continue
                    w     = _STRATEGY_SHARPE_WEIGHT.get(strategy.name, 0.5)
                    score = sig.confidence * w
                    if sym not in candidates or score > candidates[sym][0]:
                        candidates[sym] = (score, sig, strategy.name, qty)

            # Sort all candidates globally by quality score; fill open slots
            ranked = sorted(candidates.items(), key=lambda x: x[1][0], reverse=True)
            slots  = MAX_OPEN - len(self.open_positions)
            for sym, (score, sig, strat_name, qty) in ranked[:slots]:
                self._open(sym, sig, qty, strat_name, ts)

        self._print_day_summary(session_date)

    # ── Position management ───────────────────────────────────────────────────

    def _open(self, sym: str, sig: StrategySignal, qty: int,
              strat_name: str, ts) -> None:
        notional  = qty * sig.entry
        stop_dist = max(sig.entry - sig.stop_loss, 1e-6)
        risk_rs   = qty * stop_dist
        total_dep = sum(p.notional for p in self.open_positions.values()) + notional
        dep_pct   = total_dep / self.equity * 100

        pos = _Position(
            symbol=sym,
            strategy=strat_name,
            entry_price=round(sig.entry, 2),
            stop_loss=round(sig.stop_loss, 2),
            target=round(sig.target, 2),
            qty=qty,
            confidence=sig.confidence,
            rr=sig.risk_reward,
            entry_time_str=ts.strftime("%H:%M"),
            notional=round(notional, 2),
            risk_rs=round(risk_rs, 2),
        )
        self.open_positions[sym] = pos

        print(
            f"  {ts.strftime('%H:%M')}  BUY   {sym:<12} x{qty:<4} "
            f"@{sig.entry:>8.2f}  SL:{sig.stop_loss:>8.2f}  TGT:{sig.target:>8.2f}"
            f"  [{strat_name}  c={sig.confidence:.2f}  RR={sig.risk_reward:.1f}]"
        )
        print(
            f"            Risk: Rs{risk_rs:>7.2f}  "
            f"Notional: Rs{notional:>8.2f}  "
            f"Deployed: Rs{total_dep:>9.2f} ({dep_pct:.1f}%)"
        )

    def _close(self, sym: str, pos: _Position, exit_price: float,
               ts, reason: str) -> None:
        del self.open_positions[sym]

        exit_notional = pos.qty * exit_price
        costs   = _calc_trade_costs(pos.notional, exit_notional)
        gross   = round(pos.qty * (exit_price - pos.entry_price), 2)
        pnl_rs  = round(gross - costs, 2)
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price if pos.entry_price else 0.0

        self.equity      += pnl_rs
        self.peak_equity  = max(self.peak_equity, self.equity)
        self._day_pnl_rs += pnl_rs

        self.consecutive_losses = 0 if pnl_rs > 0 else self.consecutive_losses + 1

        if pnl_rs <= 0 and reason in ("stop_loss", "stale_exit"):
            self._symbol_block_until[sym] = ts + pd.Timedelta(minutes=SYMBOL_COOLDOWN_MINS)

        trade = SimTrade(
            symbol=sym, strategy=pos.strategy,
            date=str(ts.date()),
            entry_time=pos.entry_time_str, exit_time=ts.strftime("%H:%M"),
            entry_price=pos.entry_price, exit_price=round(exit_price, 2),
            qty=pos.qty, pnl_rs=pnl_rs, pnl_pct=pnl_pct,
            notional=pos.notional, exit_reason=reason,
            costs_rs=costs, gross_rs=gross,
        )
        self.all_trades.append(trade)
        self._day_trades.append(trade)

        label = "WIN " if pnl_rs > 0 else ("LOSS" if pnl_rs < 0 else "EVEN")
        gs    = "+" if gross >= 0 else ""
        ns    = "+" if pnl_rs >= 0 else ""
        print(
            f"  {ts.strftime('%H:%M')}  {label}  {sym:<12} x{pos.qty:<4} "
            f"@{exit_price:>8.2f}  ({reason:<11})  "
            f"Gross:{gs}Rs{gross:>7.2f}  Cost:-Rs{costs:.2f}  "
            f"Net:{ns}Rs{pnl_rs:>7.2f}  Eq:Rs{self.equity:>10.2f}"
        )

        self._check_cb()

    def _check_cb(self) -> None:
        if self.cb_state == "TRIPPED":
            return
        daily_loss_pct = -self._day_pnl_rs / self.initial_capital
        drawdown_pct   = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity else 0.0

        if daily_loss_pct >= CB_DAILY_LOSS_PCT:
            self.cb_state  = "TRIPPED"
            self.cb_reason = f"daily_loss ({daily_loss_pct*100:.1f}%)"
            print(f"\n  *** CIRCUIT BREAKER: daily loss {daily_loss_pct*100:.1f}% >= {CB_DAILY_LOSS_PCT*100:.0f}% ***\n")
        elif self.consecutive_losses >= CB_CONSEC_LOSSES:
            self.cb_state  = "TRIPPED"
            self.cb_reason = f"consecutive_losses ({self.consecutive_losses})"
            print(f"\n  *** CIRCUIT BREAKER: {self.consecutive_losses} consecutive losses ***\n")
        elif drawdown_pct >= CB_DRAWDOWN_PCT:
            self.cb_state  = "TRIPPED"
            self.cb_reason = f"drawdown ({drawdown_pct*100:.1f}%)"
            print(f"\n  *** CIRCUIT BREAKER: drawdown {drawdown_pct*100:.1f}% >= {CB_DRAWDOWN_PCT*100:.0f}% ***\n")
        elif daily_loss_pct >= CB_DAILY_LOSS_PCT * 0.8 or drawdown_pct >= CB_DRAWDOWN_PCT * 0.8:
            self.cb_state = "WARNING"

    # ── Reporting ─────────────────────────────────────────────────────────────

    def _print_day_summary(self, session_date) -> None:
        wins     = sum(1 for t in self._day_trades if t.pnl_rs > 0)
        losses   = sum(1 for t in self._day_trades if t.pnl_rs <= 0)
        pnl_pct  = self._day_pnl_rs / self.initial_capital * 100
        drawdown = (self.peak_equity - self.equity) / self.peak_equity * 100 if self.peak_equity else 0.0
        sign     = "+" if self._day_pnl_rs >= 0 else ""
        print()
        if self._day_trades:
            print(
                f"  Day {session_date}:  "
                f"{len(self._day_trades)} trades  "
                f"({wins}W/{losses}L)  "
                f"Daily P&L: {sign}Rs {self._day_pnl_rs:.2f} ({sign}{pnl_pct:.2f}%)  "
                f"CB: {self.cb_state}"
            )
        else:
            print(f"  Day {session_date}:  No trades taken  CB: {self.cb_state}")
        print(
            f"  Equity: Rs {self.equity:.2f}  "
            f"Peak: Rs {self.peak_equity:.2f}  "
            f"Drawdown: {drawdown:.2f}%  "
            f"Consec losses: {self.consecutive_losses}"
        )
        print("-" * 80)

        self.daily_summary.append({
            "date":          str(session_date),
            "trades":        len(self._day_trades),
            "wins":          wins,
            "losses":        losses,
            "daily_pnl_rs":  round(self._day_pnl_rs, 2),
            "daily_pnl_pct": round(pnl_pct, 3),
            "equity":        round(self.equity, 2),
            "cb_state":      self.cb_state,
        })

    def _print_final_report(self) -> None:
        sep   = "=" * 80
        total = len(self.all_trades)
        wins  = [t for t in self.all_trades if t.pnl_rs > 0]
        wr    = len(wins) / total * 100 if total else 0.0
        net   = self.equity - self.initial_capital
        pct   = net / self.initial_capital * 100

        # Max drawdown (Rs) over the equity curve
        cum = self.initial_capital
        peak_eq = self.initial_capital
        mdd_rs  = 0.0
        for t in self.all_trades:
            cum += t.pnl_rs
            peak_eq = max(peak_eq, cum)
            dd = peak_eq - cum
            if dd > mdd_rs:
                mdd_rs = dd
        mdd_pct = mdd_rs / self.initial_capital * 100

        total_costs = sum(t.costs_rs for t in self.all_trades)
        gross_pnl   = sum(t.gross_rs for t in self.all_trades)
        sign        = "+" if net >= 0 else ""
        gs          = "+" if gross_pnl >= 0 else ""
        print(f"\n{sep}")
        print(f"  WEEKLY SUMMARY  |  Rs {self.initial_capital:,.0f} -> Rs {self.equity:,.2f}")
        print(sep)
        print(f"  Total trades:        {total}")
        print(f"  Win rate:            {wr:.1f}%  ({len(wins)} wins)")
        print(f"  Gross P&L (pre-cost):{gs}Rs {gross_pnl:,.2f}")
        print(f"  Transaction costs:   -Rs {total_costs:,.2f}  ({total_costs/total:.1f}/trade avg)")
        print(f"  Net P&L (after cost):{sign}Rs {net:,.2f}  ({sign}{pct:.2f}%)")
        print(f"  Max drawdown:        Rs {mdd_rs:,.2f}  ({mdd_pct:.2f}%)")
        cb_str = self.cb_state + (f"  [{self.cb_reason}]" if self.cb_reason else "")
        print(f"  Circuit breaker:     {cb_str}")

        # Strategy breakdown
        if self.all_trades:
            strat: dict[str, dict] = {}
            for t in self.all_trades:
                s = strat.setdefault(t.strategy, {"n": 0, "w": 0, "pnl": 0.0})
                s["n"] += 1
                s["w"] += t.pnl_rs > 0
                s["pnl"] += t.pnl_rs
            print(f"\n  Strategy breakdown:")
            hdr = f"  {'Strategy':<18}  {'Trades':>6}  {'WR%':>6}  {'Net P&L':>12}"
            print(hdr)
            print(f"  {'-'*18}  {'-'*6}  {'-'*6}  {'-'*12}")
            for sname, ss in sorted(strat.items(), key=lambda x: -x[1]["pnl"]):
                wr_s  = ss["w"] / ss["n"] * 100
                sgn_s = "+" if ss["pnl"] >= 0 else ""
                print(f"  {sname:<18}  {ss['n']:>6}  {wr_s:>5.1f}%  {sgn_s}Rs {ss['pnl']:>8.2f}")

        # Symbol breakdown
        if self.all_trades:
            syms: dict[str, dict] = {}
            for t in self.all_trades:
                s = syms.setdefault(t.symbol, {"n": 0, "w": 0, "pnl": 0.0})
                s["n"] += 1
                s["w"] += t.pnl_rs > 0
                s["pnl"] += t.pnl_rs
            print(f"\n  Symbol breakdown (top 10 by P&L):")
            hdr = f"  {'Symbol':<14}  {'Trades':>6}  {'WR%':>6}  {'Net P&L':>12}"
            print(hdr)
            print(f"  {'-'*14}  {'-'*6}  {'-'*6}  {'-'*12}")
            for sym, ss in sorted(syms.items(), key=lambda x: -x[1]["pnl"])[:10]:
                wr_s  = ss["w"] / ss["n"] * 100
                sgn_s = "+" if ss["pnl"] >= 0 else ""
                print(f"  {sym:<14}  {ss['n']:>6}  {wr_s:>5.1f}%  {sgn_s}Rs {ss['pnl']:>8.2f}")

        _print_guide(self.initial_capital)
        print(sep + "\n")

    def _save_results(self, interval: str, start_date: str, end_date: str) -> None:
        out_dir = Path("data/backtest_results")
        out_dir.mkdir(parents=True, exist_ok=True)
        net         = self.equity - self.initial_capital
        total_costs = round(sum(t.costs_rs for t in self.all_trades), 2)
        gross_pnl   = round(sum(t.gross_rs for t in self.all_trades), 2)
        out = {
            "capital":            self.initial_capital,
            "interval":           interval,
            "date_range":         f"{start_date}_to_{end_date}",
            "starting_equity":    self.initial_capital,
            "ending_equity":      round(self.equity, 2),
            "gross_pnl_rs":       gross_pnl,
            "total_costs_rs":     total_costs,
            "net_pnl_rs":         round(net, 2),
            "net_pnl_pct":        round(net / self.initial_capital * 100, 3),
            "total_trades":       len(self.all_trades),
            "win_rate":           round(
                sum(1 for t in self.all_trades if t.pnl_rs > 0) / len(self.all_trades), 4
            ) if self.all_trades else 0.0,
            "circuit_breaker":    self.cb_state,
            "cb_reason":          self.cb_reason,
            "daily_summary":      self.daily_summary,
            "trades": [
                {
                    "symbol":      t.symbol,
                    "strategy":    t.strategy,
                    "date":        t.date,
                    "entry_time":  t.entry_time,
                    "exit_time":   t.exit_time,
                    "entry_price": t.entry_price,
                    "exit_price":  t.exit_price,
                    "qty":         t.qty,
                    "gross_rs":    t.gross_rs,
                    "costs_rs":    t.costs_rs,
                    "pnl_rs":      t.pnl_rs,
                    "pnl_pct":     round(t.pnl_pct * 100, 4),
                    "notional":    t.notional,
                    "exit_reason": t.exit_reason,
                }
                for t in self.all_trades
            ],
        }
        fname = f"capital_sim_1L_{interval}_{start_date}_to_{end_date}.json"
        path  = out_dir / fname
        path.write_text(json.dumps(out, indent=2))
        print(f"Saved: {path}")


# ── Module helpers ─────────────────────────────────────────────────────────────

def _size_position(equity: float, entry: float, stop_loss: float) -> int:
    """
    Return quantity using the live position-sizing formula from CLAUDE.md:
        stop_distance  = max(entry - stop_loss, entry * 0.02)   # 2% floor
        qty_by_risk    = floor(equity * 0.02 / stop_distance)   # 2% capital risk
        qty_by_notional= floor(equity * 0.05 / entry)           # 5% notional cap
        qty            = min(qty_by_risk, qty_by_notional)
    Returns 0 if 1 share alone exceeds the 5% notional cap.
    """
    stop_dist       = max(entry - stop_loss, entry * 0.02)
    qty_by_risk     = int(equity * MAX_RISK_PCT / stop_dist)
    qty_by_notional = int(equity * MAX_NOTIONAL_PCT / entry)
    return min(qty_by_risk, qty_by_notional)


def _print_banner(capital: float, interval: str, start_date: str, end_date: str) -> None:
    sep = "=" * 80
    print(f"\n{sep}")
    print(
        f"  CAPITAL SIMULATION  |  Rs {capital:,.0f}  |  "
        f"{interval} bars  |  {start_date} to {end_date}"
    )
    print(f"  Priority: MeanReversion -> MACD+RSI -> Supertrend -> VWAP")
    print(f"  Max concurrent: {MAX_OPEN}  |  Min confidence: {MIN_CONFIDENCE}  |  Min R:R: {MIN_RR}")
    print(sep)


def _print_day_header(session_date) -> None:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print(f"\n{'='*80}")
    print(f"  {days[session_date.weekday()]} {session_date}")
    print(f"{'='*80}")


def _print_guide(capital: float) -> None:
    """Print per-symbol position sizing guide and trading rules."""
    cap5 = capital * MAX_NOTIONAL_PCT
    print(f"\n  POSITION SIZING GUIDE  (Rs {capital:,.0f} capital  |  5% cap = Rs {cap5:,.0f})")
    print(f"  {'Symbol':<14}  {'~Price':>8}  {'Max qty':>8}  {'~Notional':>10}  Strategy")
    print(f"  {'-'*14}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*14}")
    rows = [
        ("TATASTEEL",   208,  "MeanReversion"),
        ("IDFCFIRSTB",   68,  "MeanReversion"),
        ("BANKBARODA",  265,  "MeanReversion"),
        ("FEDERALBNK",  289,  "MeanReversion"),
        ("VEDL",        329,  "MeanReversion"),
        ("BEL",         418,  "MeanReversion"),
        ("SBICARD",     624,  "MACD+RSI"),
        ("INDUSINDBK",  910,  "MeanReversion"),
        ("BAJFINANCE",  920,  "MeanReversion"),
        ("HINDALCO",   1098,  "MACD+RSI"),
        ("JSWSTEEL",   1286,  "MeanReversion"),
        ("ADANIPORTS", 1792,  "MeanReversion"),
        ("BAJAJFINSV", 1774,  "MeanReversion"),
        ("ADANIENT",   2734,  "MeanReversion"),
        ("HAL",        4419,  "Supertrend"),
        ("HEROMOTOCO", 4966,  "Supertrend"),
        ("EICHERMOT",  6973,  "-- SKIP (> 5% cap) --"),
    ]
    for sym, price, strat in rows:
        qty   = int(cap5 / price)
        note  = qty * price
        if qty == 0:
            print(f"  {sym:<14}  {price:>8,}  {'SKIP':>8}  {'>5% cap':>10}  {strat}")
        else:
            print(f"  {sym:<14}  {price:>8,}  {qty:>8}  Rs{note:>9,}  {strat}")

    print(f"\n  CIRCUIT BREAKERS  (at Rs {capital:,.0f}):")
    print(f"    Daily P&L loss > -Rs {capital*CB_DAILY_LOSS_PCT:,.0f}  -> HALT today")
    print(f"    5 consecutive losses              -> HALT")
    print(f"    Drawdown > Rs {capital*CB_DRAWDOWN_PCT:,.0f}            -> HALT")
    print(f"\n  ENTRY WINDOW:  10:30 AM - 2:00 PM  (sweet spot for mean reversion)")
    print(f"  HARD CUTOFF:   No new entries after 2:30 PM")
    print(f"  FORCE CLOSE:   All MIS positions at 3:15 PM")
    print(f"  AVOID:         EICHERMOT (too expensive at ~Rs 6,973 > Rs {cap5:,.0f})")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capital simulation — live-feel with Rs 1L"
    )
    parser.add_argument(
        "--capital", type=float, default=DEFAULT_CAPITAL,
        help=f"Starting capital in Rs (default: {DEFAULT_CAPITAL:,})",
    )
    parser.add_argument(
        "--week", action="store_true",
        help="Simulate the current trading week (Mon -> today)",
    )
    parser.add_argument("--start", type=str, default=None, help="Start date ISO e.g. 2026-05-19")
    parser.add_argument("--end",   type=str, default=None, help="End date ISO e.g. 2026-05-23")
    parser.add_argument(
        "--interval", type=str, default="5m",
        choices=["1m", "2m", "5m", "15m"],
        help="Bar interval (default: 5m)",
    )
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated NSE symbols (default: 20 high-beta stocks)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

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

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols else HIGH_BETA_STOCKS
    )

    print(f"\nCapital simulation: Rs {args.capital:,.0f} | {len(symbols)} symbols | {args.interval}")
    print(f"Range:   {start_date} to {end_date}")
    print(f"Symbols: {', '.join(symbols[:10])}{'  ...' if len(symbols) > 10 else ''}\n")

    sim = CapitalSimulator(capital=args.capital)
    sim.run(
        symbols=symbols,
        interval=args.interval,
        start_date=start_date,
        end_date=end_date,
    )


if __name__ == "__main__":
    main()
