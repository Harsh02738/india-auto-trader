"""
Autonomous Trade & Exit Engine.

Runs three background loops:
  1. Market scan (every 5 min, 9:15–15:10 IST)
     → Strategy consensus on Nifty 200
     → Auto-sends high-conviction cards to Telegram (user still confirms)
  2. Position monitor (every 2 min, 9:15–15:25 IST)
     → Checks each open position against live LTP
     → Auto-exits on target hit, stop hit, trailing stop, or MIS square-off
  3. EOD cleanup (15:45 IST)
     → Runs P&L tracker, updates snapshot, sends EOD report

Start standalone:
    python -m backend.trade_engine

Or import and call start_engine() from within main.py.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

SNAPSHOT_FILE  = Path("data/portfolio/snapshot.json")
PAUSE_FLAG     = Path("data/portfolio/trading_paused.flag")

# Market timing (IST)
MARKET_OPEN    = (9, 15)
SCAN_STOP      = (15, 10)
MONITOR_STOP   = (15, 25)
EOD_TIME       = (15, 45)
MIS_SQUAREOFF  = (15, 10)

# Confidence threshold for auto-sending scan alerts to Telegram
AUTO_ALERT_CONFIDENCE = 0.72
AUTO_ALERT_MIN_VOTES  = 3

# Scan interval in seconds
SCAN_INTERVAL_SEC    = 300   # 5 min
MONITOR_INTERVAL_SEC = 120   # 2 min

# Trailing stop multiplier (in units of ATR)
TRAILING_STOP_ATR = 1.5


def _ist_now() -> datetime:
    return datetime.now(tz=IST)


def _ist_hm() -> tuple[int, int]:
    n = _ist_now()
    return n.hour, n.minute


def _is_market_open() -> bool:
    h, m = _ist_hm()
    after_open  = (h, m) >= MARKET_OPEN
    before_scan = (h, m) < (16, 0)   # broad window
    return after_open and before_scan


def _is_scan_time() -> bool:
    h, m = _ist_hm()
    return MARKET_OPEN <= (h, m) < SCAN_STOP


def _is_monitor_time() -> bool:
    h, m = _ist_hm()
    return MARKET_OPEN <= (h, m) < MONITOR_STOP


def _is_mis_squareoff_time() -> bool:
    h, m = _ist_hm()
    return (h, m) >= MIS_SQUAREOFF


def _is_paused() -> bool:
    return PAUSE_FLAG.exists()


def _circuit_tripped() -> bool:
    try:
        if SNAPSHOT_FILE.exists():
            snap = json.loads(SNAPSHOT_FILE.read_text())
            return snap.get("circuit_breaker", {}).get("tripped", False)
    except Exception:
        pass
    return False


# ── Loop 1: Market Scan ────────────────────────────────────────────────────────

def _scan_loop() -> None:
    """Runs strategy engine on Nifty 200. Sends high-conviction cards to Telegram."""
    logger.info("Scan loop started")

    # Track which symbols we already sent alerts for today (avoid spam)
    alerted_today: set[str] = set()
    last_reset_date: str = ""

    while True:
        try:
            today = _ist_now().strftime("%Y-%m-%d")
            if today != last_reset_date:
                alerted_today.clear()
                last_reset_date = today

            if not _is_scan_time():
                time.sleep(60)
                continue

            if _is_paused() or _circuit_tripped():
                time.sleep(SCAN_INTERVAL_SEC)
                continue

            logger.info("Running market scan…")
            _run_scan(alerted_today)

        except Exception as exc:
            logger.error("Scan loop error: %s", exc)

        time.sleep(SCAN_INTERVAL_SEC)


def _run_scan(alerted_today: set[str]) -> None:
    from config.instruments import NIFTY_200
    from data_collector.market_data import collect_daily
    from strategies.engine import StrategyEngine
    from monitoring.telegram_bot import send_analysis_card

    engine = StrategyEngine()
    symbols = [inst.symbol for inst in NIFTY_200]

    for symbol in symbols:
        if symbol in alerted_today:
            continue

        try:
            # Load cached OHLCV (refreshed by data_collector on separate schedule)
            cache = Path(f"data/market/{symbol}_ohlcv.json")
            if not cache.exists():
                continue
            ohlcv = json.loads(cache.read_text())

            # Load fundamentals if available
            fund_path = Path(f"data/fundamentals/{symbol}_fund.json")
            fundamentals = None
            if fund_path.exists():
                fundamentals = json.loads(fund_path.read_text())

            consensus = engine.evaluate(symbol, ohlcv, fundamentals)

            # Send Telegram alert only for high-conviction signals
            if (
                consensus.action != "HOLD"
                and consensus.combined_confidence >= AUTO_ALERT_CONFIDENCE
                and consensus.vote_count >= AUTO_ALERT_MIN_VOTES
            ):
                logger.info(
                    "High-conviction %s: %s conf=%.2f votes=%d/%d",
                    consensus.action, symbol,
                    consensus.combined_confidence, consensus.vote_count, consensus.total_strategies
                )
                send_analysis_card(consensus)
                alerted_today.add(symbol)

        except Exception as exc:
            logger.debug("Scan error for %s: %s", symbol, exc)


# ── Loop 2: Position Monitor ───────────────────────────────────────────────────

def _monitor_loop() -> None:
    """Monitors open positions for exits: target, stop, trailing, MIS square-off."""
    logger.info("Position monitor loop started")

    # Track trailing stops per symbol: symbol → current SL price
    trailing_stops: dict[str, float] = {}

    while True:
        try:
            if not _is_monitor_time():
                time.sleep(60)
                continue

            _check_positions(trailing_stops)

        except Exception as exc:
            logger.error("Monitor loop error: %s", exc)

        time.sleep(MONITOR_INTERVAL_SEC)


def _check_positions(trailing_stops: dict[str, float]) -> None:
    from broker.kotak_direct import KotakBroker
    from monitoring.alerts import alert_stop_hit
    from supabase_client import close_trade, get_open_trades
    import asyncio as _asyncio

    broker = KotakBroker()
    positions = broker.get_positions()

    if not positions:
        return

    mis_squareoff = _is_mis_squareoff_time()

    for pos in positions:
        if not isinstance(pos, dict):
            continue

        # Parse position fields
        sym_raw = pos.get("trdSym") or pos.get("symbol") or pos.get("tradingSymbol") or ""
        symbol  = sym_raw.replace("-EQ", "").upper()
        qty_raw = int(pos.get("flBuyQty") or pos.get("netQty") or pos.get("quantity") or 0)
        product = (pos.get("product") or pos.get("prd") or "MIS").upper()
        avg_price = float(pos.get("avgPrice") or pos.get("average_price") or 0)

        if qty_raw == 0 or not symbol:
            continue

        # Force MIS square-off before 15:10
        if product == "MIS" and mis_squareoff:
            logger.info("MIS square-off: %s qty=%d", symbol, qty_raw)
            result = broker.place_order(symbol, "SELL", abs(qty_raw), 0, "MKT", "MIS", tag="AUTO_SQUAREOFF")
            order_id = broker.extract_order_id(result)
            if order_id:
                _close_position(broker, symbol, order_id, avg_price, qty_raw, close_trade, alert_stop_hit)
            continue

        # Get live price
        ltp = broker.get_ltp(symbol)
        if ltp is None:
            continue

        # Load signal file for target and initial stop
        sig_path = Path(f"data/signals/{symbol}_signal.json")
        sig_data: dict = {}
        if sig_path.exists():
            try:
                sig_data = json.loads(sig_path.read_text())
            except Exception:
                pass

        target_price = sig_data.get("target") or (avg_price * 1.05)
        initial_sl   = sig_data.get("stop_loss") or (avg_price * 0.97)

        # Load ATR for trailing stop
        ohlcv: dict = {}
        cache = Path(f"data/market/{symbol}_ohlcv.json")
        if cache.exists():
            try:
                ohlcv = json.loads(cache.read_text())
            except Exception:
                pass
        atr = ohlcv.get("atr") or avg_price * 0.02

        # Update trailing stop: move SL up when in profit
        current_sl = trailing_stops.get(symbol, initial_sl)
        if ltp > avg_price:
            new_trail = round(ltp - TRAILING_STOP_ATR * atr, 2)
            if new_trail > current_sl:
                trailing_stops[symbol] = new_trail
                current_sl = new_trail
                logger.debug("Trailing SL updated: %s → %.2f", symbol, current_sl)

        exit_reason: str | None = None

        if ltp >= target_price:
            exit_reason = f"TARGET HIT ₹{ltp:.2f} ≥ ₹{target_price:.2f}"
        elif ltp <= current_sl:
            exit_reason = f"STOP HIT ₹{ltp:.2f} ≤ ₹{current_sl:.2f}"

        if exit_reason:
            logger.info("Exit triggered %s: %s", symbol, exit_reason)
            result = broker.place_order(symbol, "SELL", abs(qty_raw), 0, "MKT", product, tag="AUTO_EXIT")
            order_id = broker.extract_order_id(result)
            pnl = (ltp - avg_price) * abs(qty_raw)
            if order_id:
                try:
                    close_trade(order_id, ltp, pnl)
                except Exception:
                    pass
                try:
                    _asyncio.run(alert_stop_hit(symbol, pnl))
                except Exception:
                    pass
            trailing_stops.pop(symbol, None)
            logger.info("Exit done %s P&L=₹%.0f reason=%s", symbol, pnl, exit_reason)


def _close_position(broker, symbol, order_id, avg_price, qty, close_trade_fn, alert_fn) -> None:
    from broker.kotak_direct import KotakBroker
    import asyncio as _asyncio
    ltp = broker.get_ltp(symbol) or avg_price
    pnl = (ltp - avg_price) * abs(qty)
    try:
        close_trade_fn(order_id, ltp, pnl)
    except Exception:
        pass
    try:
        _asyncio.run(alert_fn(symbol, pnl))
    except Exception:
        pass


# ── Loop 3: EOD Cleanup ────────────────────────────────────────────────────────

def _eod_loop() -> None:
    """Runs once daily at 15:45 IST for EOD report and cleanup."""
    logger.info("EOD loop started")
    eod_done_date: str = ""

    while True:
        try:
            now = _ist_now()
            today = now.strftime("%Y-%m-%d")
            h, m = now.hour, now.minute

            if (h, m) >= EOD_TIME and today != eod_done_date:
                logger.info("Running EOD cleanup…")
                _run_eod()
                eod_done_date = today

        except Exception as exc:
            logger.error("EOD loop error: %s", exc)

        time.sleep(300)   # check every 5 minutes


def _run_eod() -> None:
    import asyncio as _asyncio
    try:
        from monitoring.pnl_tracker import run as run_pnl
        run_pnl()
        logger.info("EOD P&L tracker completed")
    except Exception as exc:
        logger.warning("EOD P&L tracker error: %s", exc)


# ── Engine start ───────────────────────────────────────────────────────────────

_engine_started = False
_engine_lock = threading.Lock()


def start_engine() -> None:
    """Start all three loops as daemon threads. Safe to call multiple times."""
    global _engine_started
    with _engine_lock:
        if _engine_started:
            return
        _engine_started = True

    threads = [
        threading.Thread(target=_scan_loop,    name="scan-loop",    daemon=True),
        threading.Thread(target=_monitor_loop, name="monitor-loop", daemon=True),
        threading.Thread(target=_eod_loop,     name="eod-loop",     daemon=True),
    ]
    for t in threads:
        t.start()
        logger.info("Started thread: %s", t.name)

    logger.info("Trade engine running — 3 background loops active")


# ── Standalone entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    from monitoring.telegram_bot import start_bot_thread
    start_bot_thread()

    start_engine()
    logger.info("Trade engine running standalone. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down trade engine.")
        sys.exit(0)
