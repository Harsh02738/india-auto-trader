"""
Continuous intraday scanner — runs every 5 minutes during NSE market hours (9:15–3:30 IST).

Each cycle:
  1. Collects fresh 5-min OHLCV for all symbols  → data/market/{SYM}_intraday.json
  2. Every 15 min (3 cycles): refreshes RSS sentiment → data/sentiment/{SYM}_sent.json
  3. Scores each symbol (intraday technicals + daily EMA-200 + fundamentals + sentiment)
  4. Upserts signals to Supabase (live dashboard updates via Realtime)
  5. NEW signal → sends Telegram alert with [✅ Go / ❌ Skip] inline buttons
  6. User taps "Go" → symbol is queued → next cycle executes via Kotak Neo

Usage:
    python -m data_collector.intraday_scanner
    python -m data_collector.intraday_scanner --symbols RELIANCE TCS HDFCBANK
    python -m data_collector.intraday_scanner --universe nifty50   (default)
    python -m data_collector.intraday_scanner --universe nifty200
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.instruments import NIFTY_50, NIFTY_200
from monitoring.telegram_bot import start_bot_thread, send_signal_alert, send_text, pop_approved

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

IST             = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN     = (9, 15)    # IST
MARKET_CLOSE    = (15, 30)   # IST
SCAN_INTERVAL   = 300        # seconds between cycles
SENTIMENT_EVERY = 3          # refresh sentiment every N cycles (= 15 min)

DATA_DIR = Path("data")

SECTOR_PE: dict[str, float] = {
    "IT": 30, "FMCG": 47, "Banking": 20, "NBFC": 28,
    "Energy": 14, "Oil & Gas": 14, "Metals": 12, "Mining": 12,
    "Auto": 23, "Pharma": 25, "Capital Goods": 28, "Defence": 45,
    "Power": 20, "Renewable Energy": 60, "Real Estate": 25,
    "Telecom": 22, "Chemicals": 35, "Healthcare": 35,
    "Insurance": 30, "Consumer": 40, "Hotels": 35, "Retail": 50,
    "Conglomerate": 27, "Infrastructure": 27, "Auto Ancillary": 20,
    "Cement": 23,
}
SECTOR_MAP:    dict[str, str] = {inst.symbol: inst.sector for inst in NIFTY_200}
COMPANY_NAMES: dict[str, str] = {inst.symbol: inst.name  for inst in NIFTY_200}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _g(d: dict, key: str, default=None):
    v = d.get(key, default)
    return default if v is None else v


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def is_market_open() -> bool:
    t = now_ist()
    if t.weekday() >= 5:  # Saturday / Sunday
        return False
    h, m = t.hour, t.minute
    after_open  = (h, m) >= MARKET_OPEN
    before_close = (h, m) < MARKET_CLOSE
    return after_open and before_close


def minutes_to_open() -> int:
    """Minutes until next market open (positive = future, 0 if already open)."""
    t = now_ist()
    # If weekend, skip to Monday
    days_ahead = (7 - t.weekday()) % 7 if t.weekday() >= 5 else 0
    open_today = t.replace(
        hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0
    )
    if days_ahead == 0 and t >= open_today:
        return 0
    target = open_today + timedelta(days=days_ahead)
    return max(0, int((target - t).total_seconds() // 60))




# ── Scoring ───────────────────────────────────────────────────────────────────

def score_symbol(sym: str) -> dict:
    """
    Score a symbol using:
     - Intraday 5-min OHLCV (_intraday.json) for fresh RSI/MACD/volume/price
     - Daily EOD (_ohlcv.json) for above_ema200 context
     - Fundamentals (_fund.json)
     - Sentiment (_sent.json)
    """
    intraday = _load(DATA_DIR / "market" / f"{sym}_intraday.json")
    daily    = _load(DATA_DIR / "market" / f"{sym}_ohlcv.json")
    fund     = _load(DATA_DIR / "fundamentals" / f"{sym}_fund.json")
    sent     = _load(DATA_DIR / "sentiment" / f"{sym}_sent.json")

    # Prefer intraday for fresh price/technical data, fall back to daily
    mkt = intraday if intraday else daily

    rsi            = _g(mkt, "rsi", 50.0)
    macd_hist      = _g(mkt, "macd_hist", 0.0)
    macd_crossover = _g(mkt, "macd_crossover", False)
    vol_ratio      = _g(mkt, "vol_ratio", 1.0)
    bb_pct         = _g(mkt, "bb_pct", 0.5)
    ltp            = _g(mkt, "last_close", 0.0)
    atr            = _g(mkt, "atr", ltp * 0.015 if ltp else 1.0)

    # EMA-200 from daily file (not available on 5-min timeframe with 5d period)
    above_ema200   = _g(daily, "above_ema200", _g(mkt, "above_ema200", False))

    # ── Technical score ────────────────────────────────────────────────────────
    t = 0.0
    if rsi < 30:       t += 0.30
    elif rsi < 40:     t += 0.20
    elif rsi < 50:     t += 0.10
    elif rsi > 75:     t -= 0.20
    elif rsi > 65:     t -= 0.10

    if macd_crossover:   t += 0.25
    elif macd_hist > 0:  t += 0.15
    elif macd_hist < 0:  t -= 0.10

    if above_ema200:   t += 0.20
    else:              t -= 0.05

    if vol_ratio and vol_ratio > 1.5:  t += 0.15
    elif vol_ratio and vol_ratio > 1.2: t += 0.05

    if bb_pct < 0.2:   t += 0.10
    elif bb_pct > 0.8: t -= 0.10

    t = max(0.0, min(1.0, t + 0.15))

    # ── Fundamental score ──────────────────────────────────────────────────────
    f = _g(fund, "fundamental_score", None)
    if f is None:
        pe         = _g(fund, "pe_ratio", 0)
        roe        = _g(fund, "roe", 0)
        de         = _g(fund, "de_ratio", None)
        rev_growth = _g(fund, "revenue_growth", 0) or _g(fund, "earnings_growth", 0)
        bench_pe   = SECTOR_PE.get(SECTOR_MAP.get(sym, ""), 20)
        f = 0.45
        if pe and bench_pe:
            ratio = pe / bench_pe
            if ratio < 0.7:   f += 0.25
            elif ratio < 0.9: f += 0.15
            elif ratio > 1.6: f -= 0.15
        if roe:
            if roe > 0.20:    f += 0.20
            elif roe > 0.12:  f += 0.10
        if isinstance(de, float) and de < 1.0: f += 0.15
        if rev_growth and rev_growth > 0.10:   f += 0.15
        elif rev_growth and rev_growth > 0:    f += 0.05
        f = max(0.0, min(1.0, f))

    # ── Sentiment score ────────────────────────────────────────────────────────
    s = _g(sent, "sentiment_score", 0.5)

    # ── Composite ─────────────────────────────────────────────────────────────
    composite = round(0.35 * t + 0.30 * f + 0.35 * s, 4)

    action     = "BUY"  if composite >= 0.56 else ("SELL" if composite <= 0.42 else "HOLD")
    confidence = "HIGH" if composite > 0.65  else ("MEDIUM" if composite > 0.55 else "LOW")

    entry  = round(ltp, 2) if ltp else None
    sl     = round(ltp - 1.5 * atr, 2) if ltp and atr else None
    target = round(ltp + 2.5 * atr, 2) if ltp and atr else None
    rr     = round((target - ltp) / (ltp - sl), 2) if sl and target and ltp and (ltp - sl) > 0 else None

    reasoning = (
        f"RSI {rsi:.1f} ({'oversold' if rsi < 40 else 'overbought' if rsi > 65 else 'neutral'}), "
        f"MACD hist {macd_hist:.2f} ({'bullish cross' if macd_crossover else 'positive' if macd_hist > 0 else 'negative'}), "
        f"{'ABOVE' if above_ema200 else 'BELOW'} EMA-200, "
        f"Vol {vol_ratio:.1f}x, Sent {s:.3f} ({'positive' if s > 0.55 else 'negative' if s < 0.45 else 'neutral'})"
    )

    return {
        "symbol": sym, "tier": "EQUITY", "action": action,
        "composite_score": composite,
        "technical_score": round(t, 3),
        "fundamental_score": round(f, 3),
        "sentiment_score": round(s, 3),
        "confidence": confidence,
        "entry_price": entry, "stop_loss": sl, "target": target, "risk_reward": rr,
        "rsi": round(rsi, 1), "above_ema200": above_ema200,
        "macd_crossover": macd_crossover, "vol_ratio": round(vol_ratio or 1.0, 2),
        "pe": round(_g(fund, "pe_ratio", 0), 1) or None,
        "reasoning": reasoning, "executed": False,
    }


# ── Collection helpers ────────────────────────────────────────────────────────

def _collect_ohlcv(symbols: list[str]) -> None:
    from data_collector.market_data import collect_intraday_5m
    for sym in symbols:
        try:
            collect_intraday_5m(sym)
        except Exception as exc:
            logger.warning("OHLCV failed %s: %s", sym, exc)


def _collect_sentiment(symbols: list[str]) -> None:
    from data_collector.social_sentiment import collect_sentiment
    for sym in symbols:
        try:
            collect_sentiment(sym, COMPANY_NAMES.get(sym, ""))
        except Exception as exc:
            logger.warning("Sentiment failed %s: %s", sym, exc)


def _morning_daily_collect(symbols: list[str]) -> None:
    """Run once before market opens: collect 1-year daily data for EMA-200."""
    from data_collector.market_data import collect_daily
    logger.info("Morning collection: daily OHLCV for %d symbols ...", len(symbols))
    for sym in symbols:
        try:
            collect_daily(sym)
        except Exception as exc:
            logger.warning("Daily collect failed %s: %s", sym, exc)
    logger.info("Morning collection done.")


# ── Scan cycle ────────────────────────────────────────────────────────────────

def run_scan_cycle(
    symbols: list[str],
    cycle: int,
    prev_actions: dict[str, str],
) -> list[dict]:
    """
    One full scan pass. Returns list of signals that newly crossed BUY/SELL.
    """
    from local_db import upsert_signal

    t0 = time.time()
    logger.info("── Cycle %d  %s  (%d symbols) ──", cycle, now_ist().strftime("%H:%M:%S IST"), len(symbols))

    # 1. Fresh intraday OHLCV every cycle
    _collect_ohlcv(symbols)

    # 2. Sentiment refresh every 3 cycles (15 min)
    if cycle % SENTIMENT_EVERY == 0:
        logger.info("Refreshing sentiment (%d symbols)...", len(symbols))
        _collect_sentiment(symbols)

    # 3. Score and upsert
    results: list[dict] = []
    new_signals: list[dict] = []

    for sym in symbols:
        try:
            sig = score_symbol(sym)
        except Exception as exc:
            logger.warning("Scoring failed %s: %s", sym, exc)
            continue

        # Persist signal JSON locally
        sig_dir = DATA_DIR / "signals"
        sig_dir.mkdir(exist_ok=True)
        (sig_dir / f"{sym}_signal.json").write_text(json.dumps(sig, indent=2))

        # Push to Supabase
        try:
            upsert_signal(sig)
        except Exception as exc:
            logger.warning("Supabase upsert failed %s: %s", sym, exc)

        # Detect new BUY/SELL crossings
        prev = prev_actions.get(sym, "HOLD")
        if sig["action"] != "HOLD" and sig["action"] != prev:
            new_signals.append(sig)
        prev_actions[sym] = sig["action"]

        results.append(sig)

    # 4. Print compact summary
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    buys  = [r for r in results if r["action"] == "BUY"]
    sells = [r for r in results if r["action"] == "SELL"]
    elapsed = time.time() - t0

    print(f"\n[{now_ist().strftime('%H:%M:%S IST')}]  Cycle {cycle}  ({elapsed:.0f}s)")
    print(f"  BUY  ({len(buys)}):  {', '.join(r['symbol'] for r in buys[:5]) or 'None'}")
    print(f"  SELL ({len(sells)}): {', '.join(r['symbol'] for r in sells[:3]) or 'None'}")

    if new_signals:
        for sig in new_signals:
            tag  = "📈" if sig["action"] == "BUY" else "📉"
            rr   = f"  R:R {sig['risk_reward']}" if sig["risk_reward"] else ""
            print(f"  {tag} NEW SIGNAL: {sig['action']} {sig['symbol']} "
                  f"score={sig['composite_score']:.3f} [{sig['confidence']}]{rr}")

    return new_signals


# ── Telegram for new signals ──────────────────────────────────────────────────

def _alert_new_signals(new_signals: list[dict]) -> None:
    for sig in new_signals:
        send_signal_alert(sig)  # sends with [✅ Go / ❌ Skip] inline buttons


# ── Main loop ─────────────────────────────────────────────────────────────────

def _execute_approved(approved_syms: set[str], signals_by_sym: dict[str, dict]) -> None:
    """Place Kotak Neo orders for symbols the user approved via Telegram."""
    for sym in approved_syms:
        sig = signals_by_sym.get(sym)
        if not sig:
            logger.warning("Approved trade %s but no signal found — skipping", sym)
            continue
        try:
            # Kotak Neo execution (requires live credentials)
            from mcp_server.kotak_mcp import place_order, place_stop_loss
            result = place_order(
                symbol=sym,
                action=sig["action"],
                qty=sig.get("quantity", 1),
                price=sig["entry_price"] or 0,
                order_type="L",
                product="MIS",
                tag="CLAUDE_TELEGRAM",
            )
            order_id = (result or {}).get("order_id", "")
            if order_id and sig.get("stop_loss"):
                sl_action = "SELL" if sig["action"] == "BUY" else "BUY"
                place_stop_loss(sym, sl_action, sig.get("quantity", 1), sig["stop_loss"], "MIS")
            send_text(
                f"✅ <b>Executed: {sig['action']} {sym}</b>\n"
                f"Order ID: <code>{order_id or 'pending'}</code>\n"
                f"Entry ₹{sig['entry_price']}  SL ₹{sig.get('stop_loss', '—')}"
            )
            logger.info("Executed approved trade: %s %s order_id=%s", sig["action"], sym, order_id)
        except Exception as exc:
            logger.error("Execution failed for %s: %s", sym, exc)
            send_text(
                f"⚠️ <b>Execution failed: {sym}</b>\n"
                f"Error: <code>{exc}</code>\n"
                "Check Kotak Neo credentials and try manually."
            )


def main(symbols: list[str]) -> None:
    prev_actions:    dict[str, str] = {}
    last_signals:    dict[str, dict] = {}   # sym → latest signal, for trade execution
    cycle            = 0
    morning_done     = False
    eod_sent         = False

    # Start Telegram bot in background thread
    start_bot_thread()

    logger.info("Intraday scanner started — %d symbols", len(symbols))
    logger.info("Market hours: %02d:%02d–%02d:%02d IST | scan every %d sec",
                *MARKET_OPEN, *MARKET_CLOSE, SCAN_INTERVAL)

    while True:
        t = now_ist()

        # ── Morning one-time daily data collection ────────────────────────────
        if not morning_done and (9, 0) <= (t.hour, t.minute) < MARKET_OPEN:
            _morning_daily_collect(symbols)
            morning_done = True
            eod_sent = False

        if is_market_open():
            cycle += 1
            morning_done = True

            # Check for user-approved trades BEFORE the scan so we have latest signals
            approved = pop_approved()
            if approved:
                _execute_approved(approved, last_signals)

            new_sigs = run_scan_cycle(symbols, cycle, prev_actions)

            # Keep latest signals in memory for execution lookup
            for sig in new_sigs:
                last_signals[sig["symbol"]] = sig

            if new_sigs:
                _alert_new_signals(new_sigs)

            time.sleep(SCAN_INTERVAL)

        else:
            # Market closed — send EOD report once after 3:30 PM
            if (t.hour, t.minute) >= MARKET_CLOSE and not eod_sent and cycle > 0:
                buys  = sum(1 for a in prev_actions.values() if a == "BUY")
                sells = sum(1 for a in prev_actions.values() if a == "SELL")
                send_text(
                    f"📊 <b>EOD Scan Summary — {t.strftime('%d %b %Y')}</b>\n"
                    f"Cycles run: <code>{cycle}</code>\n"
                    f"Final signals — BUY: <code>{buys}</code>  SELL: <code>{sells}</code>\n"
                    f"HOLD: <code>{len(symbols) - buys - sells}</code>"
                )
                logger.info("EOD: %d cycles run. BUY=%d SELL=%d", cycle, buys, sells)
                eod_sent = True
                cycle = 0
                morning_done = False
                prev_actions.clear()
                last_signals.clear()

            mins = minutes_to_open()
            if mins > 0:
                logger.info("Market closed. Next open in ~%d min. Sleeping 60s...", mins)
            time.sleep(60)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="NSE intraday 5-min scanner")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to scan")
    parser.add_argument(
        "--universe",
        choices=["nifty50", "nifty200"],
        default="nifty50",
        help="Pre-defined symbol universe (default: nifty50)",
    )
    args = parser.parse_args()

    if args.symbols:
        scan_symbols = [s.upper() for s in args.symbols]
    elif args.universe == "nifty200":
        scan_symbols = [inst.symbol for inst in NIFTY_200]
    else:
        scan_symbols = [inst.symbol for inst in NIFTY_50]

    print(f"India Auto-Trader — Intraday Scanner")
    print(f"Universe: {len(scan_symbols)} symbols | Interval: {SCAN_INTERVAL//60} min")
    print(f"Market hours: {MARKET_OPEN[0]:02d}:{MARKET_OPEN[1]:02d}–{MARKET_CLOSE[0]:02d}:{MARKET_CLOSE[1]:02d} IST")
    print("─" * 60)

    try:
        main(scan_symbols)
    except KeyboardInterrupt:
        print("\nScanner stopped.")
        sys.exit(0)
