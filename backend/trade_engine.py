"""
Autonomous Trade & Exit Engine — Paper Trading Edition.

Three background loops:
  1. Market scan (every 2 min, 9:15–15:00 IST)
     → Strategy consensus on daily-picked stocks (loaded at 10:00 AM)
     → Auto-executes high-conviction signals via PaperBroker (no Telegram approval needed)
     → Sends Telegram notification AFTER execution
  2. Position monitor (every 2 min, 9:15–15:25 IST)
     → Checks each open position against live LTP
     → Auto-exits on target hit, stop hit, trailing stop, or MIS square-off
  3. EOD cleanup (15:45 IST)
     → Runs P&L tracker, updates snapshot, sends EOD report

Paper trading: set PAPER_TRADING=True in .env (default).
Stock universe: loaded from daily_stock_picker.py at 10:00 AM each day.
"""

from __future__ import annotations

import asyncio as _asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

SNAPSHOT_FILE = Path("data/portfolio/snapshot.json")
PAUSE_FLAG    = Path("data/portfolio/trading_paused.flag")
SIGNALS_DIR   = Path("data/signals")
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)

# Market timing (IST)
MARKET_OPEN    = (9, 15)
SCAN_STOP      = (15, 0)    # no new entries after 15:00
MONITOR_STOP   = (15, 25)
EOD_TIME       = (15, 45)
MIS_SQUAREOFF  = (15, 10)
STOCK_PICK_HOUR = settings.stock_picker_hour   # 10 AM

# Confidence threshold for autonomous execution (equity only, paper trading)
AUTO_EXEC_CONFIDENCE = 0.70
AUTO_EXEC_MIN_VOTES  = 3

# Loop intervals
SCAN_INTERVAL_SEC    = 120   # 2 min (was 5 min)
MONITOR_INTERVAL_SEC = 120   # 2 min

TRAILING_STOP_ATR = settings.trailing_stop_atr_mult

# ── Module-level state (shared across loops) ───────────────────────────────────
_daily_stocks: list[str] = []
_stock_pick_done_date: str = ""
_broker = None
_broker_lock = threading.Lock()


def _ist_now() -> datetime:
    return datetime.now(tz=IST)


def _ist_hm() -> tuple[int, int]:
    n = _ist_now()
    return n.hour, n.minute


def _is_scan_time() -> bool:
    h, m = _ist_hm()
    return MARKET_OPEN <= (h, m) < SCAN_STOP


def _is_monitor_time() -> bool:
    h, m = _ist_hm()
    return MARKET_OPEN <= (h, m) < MONITOR_STOP


def _is_mis_squareoff_time() -> bool:
    return _ist_hm() >= MIS_SQUAREOFF


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


def _get_broker():
    global _broker
    with _broker_lock:
        if _broker is None:
            if settings.paper_trading:
                from broker.paper_broker import PaperBroker
                _broker = PaperBroker()
                logger.info("[Engine] Using PaperBroker (paper trading mode)")
            else:
                from broker.kotak_direct import KotakBroker
                _broker = KotakBroker()
                logger.info("[Engine] Using KotakBroker (LIVE trading mode)")
    return _broker


# ── Stock picking at 10:00 AM ──────────────────────────────────────────────────

def _maybe_pick_stocks() -> None:
    global _daily_stocks, _stock_pick_done_date
    today = _ist_now().strftime("%Y-%m-%d")
    h, m  = _ist_hm()

    if today == _stock_pick_done_date:
        return   # already picked today

    # Run at 10:00 AM or later (first opportunity)
    if h < STOCK_PICK_HOUR:
        return

    try:
        from daily_stock_picker import pick_stocks_for_today
        symbols = pick_stocks_for_today()
        _daily_stocks = symbols
        _stock_pick_done_date = today

        # Update realtime collector with new symbol list
        try:
            from data_collector.kotak_realtime import get_collector
            get_collector().update_symbols(symbols)
        except Exception as exc:
            logger.debug("[Engine] Realtime collector update: %s", exc)

        logger.info("[Engine] Daily stocks picked (%d): %s", len(symbols), symbols)

        # Notify via Telegram
        try:
            from monitoring.telegram_bot import send_text
            send_text(
                f"📋 Today's trading universe ({len(symbols)} stocks):\n"
                + ", ".join(symbols)
                + "\n[PAPER TRADING]"
            )
        except Exception:
            pass

    except Exception as exc:
        logger.error("[Engine] Stock picker failed: %s", exc)
        # Fallback
        if not _daily_stocks:
            from daily_stock_picker import load_today_stocks
            _daily_stocks = load_today_stocks()


def _get_scan_symbols() -> list[str]:
    """Return today's stocks, or fallback universe."""
    if _daily_stocks:
        return _daily_stocks
    # Pre-10 AM: load whatever we have
    try:
        from daily_stock_picker import load_today_stocks
        return load_today_stocks()
    except Exception:
        return ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS"]


# ── Loop 1: Market Scan (autonomous execution) ─────────────────────────────────

def _scan_loop() -> None:
    logger.info("[Scan] Loop started — 2-min intraday scan with autonomous execution")
    alerted_today: set[str] = set()
    last_reset_date: str = ""

    while True:
        try:
            today = _ist_now().strftime("%Y-%m-%d")
            if today != last_reset_date:
                alerted_today.clear()
                last_reset_date = today

            _maybe_pick_stocks()

            if not _is_scan_time():
                time.sleep(60)
                continue

            if _is_paused() or _circuit_tripped():
                time.sleep(SCAN_INTERVAL_SEC)
                continue

            _run_scan(alerted_today)

        except Exception as exc:
            logger.error("[Scan] Loop error: %s", exc)

        time.sleep(SCAN_INTERVAL_SEC)


def _run_scan(alerted_today: set[str]) -> None:
    from strategies.engine import StrategyEngine
    from risk.position_sizer import PositionSizer

    engine = StrategyEngine(min_votes=AUTO_EXEC_MIN_VOTES)
    sizer  = PositionSizer()
    broker = _get_broker()
    symbols = _get_scan_symbols()

    # Respect max positions limit
    try:
        open_positions = broker.get_positions()
        if len(open_positions) >= settings.max_positions:
            logger.debug("[Scan] Max positions reached (%d) — skipping scan", len(open_positions))
            return
    except Exception:
        open_positions = []

    for symbol in symbols:
        if symbol in alerted_today:
            continue

        # Don't open a second position in the same stock
        already_open = any(
            (p.get("symbol") or "").replace("-EQ", "").upper() == symbol
            for p in open_positions
        )
        if already_open:
            continue

        try:
            # Load realtime OHLCV (from kotak_realtime.py or cached yfinance data)
            cache = Path(f"data/market/{symbol}_ohlcv.json")
            if not cache.exists():
                continue
            ohlcv = json.loads(cache.read_text())

            # Load fundamentals if available (not critical for intraday)
            fund_path = Path(f"data/fundamentals/{symbol}_fund.json")
            fundamentals = json.loads(fund_path.read_text()) if fund_path.exists() else None

            consensus = engine.evaluate(symbol, ohlcv, fundamentals)

            if (
                consensus.action != "HOLD"
                and consensus.combined_confidence >= AUTO_EXEC_CONFIDENCE
                and consensus.vote_count >= AUTO_EXEC_MIN_VOTES
            ):
                logger.info(
                    "[Scan] %s signal: %s conf=%.2f votes=%d/%d",
                    symbol, consensus.action,
                    consensus.combined_confidence, consensus.vote_count, consensus.total_strategies
                )
                _execute_signal(broker, sizer, consensus)
                alerted_today.add(symbol)

        except Exception as exc:
            logger.debug("[Scan] Error for %s: %s", symbol, exc)


def _execute_signal(broker, sizer, consensus) -> None:
    """Execute a paper trade autonomously and notify via Telegram."""
    from local_db import record_trade, upsert_signal, broadcast_event

    symbol = consensus.symbol
    action = consensus.action
    entry  = consensus.entry
    sl     = consensus.stop_loss
    target = consensus.target

    # Size position
    try:
        equity = broker.get_account_equity()
        qty = sizer.size_equity(equity, entry, sl, settings.max_account_risk_pct)
    except Exception:
        qty = max(1, int(500_000 * 0.02 / max(abs(entry - sl), entry * 0.01)))

    if qty <= 0:
        logger.debug("[Exec] %s: qty=0 after sizing — skip", symbol)
        return

    try:
        result = broker.place_order(
            symbol=symbol, action=action, qty=qty,
            price=entry, order_type="MKT", product="MIS", tag="AUTO_PAPER",
        )
        order_id = broker.extract_order_id(result) if hasattr(broker, "extract_order_id") else result.get("order_id")
        if not order_id:
            order_id = result.get("order_id", f"PAPER_{symbol}_{int(time.time())}")

        # Place stop-loss
        sl_action = "SELL" if action == "BUY" else "BUY"
        broker.place_stop_loss(symbol, sl_action, qty, sl, "MIS")

        # Write signal file
        sig_payload = {
            "symbol":         symbol,
            "tier":           "EQUITY",
            "timestamp":      _ist_now().isoformat(),
            "action":         action,
            "entry_price":    entry,
            "stop_loss":      sl,
            "target":         target,
            "quantity":       qty,
            "composite_score": consensus.combined_confidence,
            "risk_reward":    consensus.risk_reward,
            "reasoning":      consensus.reasoning,
            "executed":       True,
            "order_id":       order_id,
            "paper":          settings.paper_trading,
        }
        (SIGNALS_DIR / f"{symbol}_signal.json").write_text(json.dumps(sig_payload, indent=2))

        # Record in local_db
        record_trade({
            "order_id":       order_id,
            "symbol":         symbol,
            "tier":           "EQUITY",
            "action":         action,
            "product":        "MIS",
            "qty":            qty,
            "entry_price":    entry,
            "stop_loss":      sl,
            "target":         target,
            "composite_score": consensus.combined_confidence,
            "reasoning":      consensus.reasoning,
            "tag":            "AUTO_PAPER" if settings.paper_trading else "AUTO",
        })

        # Broadcast to WebSocket listeners
        broadcast_event("trade_executed", {
            "symbol": symbol, "action": action, "entry": entry,
            "sl": sl, "target": target, "qty": qty, "confidence": consensus.combined_confidence,
            "paper": settings.paper_trading,
        })

        # Telegram notification (no approval needed)
        _notify_execution(symbol, action, qty, entry, sl, target, consensus.combined_confidence)

        logger.info("[Exec] %s %s %d @ %.2f SL=%.2f T=%.2f [%s]",
                    action, symbol, qty, entry, sl, target,
                    "PAPER" if settings.paper_trading else "LIVE")

    except Exception as exc:
        logger.error("[Exec] Failed to execute %s %s: %s", action, symbol, exc)


def _notify_execution(symbol, action, qty, entry, sl, target, confidence) -> None:
    try:
        from monitoring.telegram_bot import send_text
        paper_tag = "[PAPER] " if settings.paper_trading else ""
        rr = round((target - entry) / max(abs(entry - sl), 0.01), 2)
        send_text(
            f"{paper_tag}✅ AUTO-EXECUTED\n"
            f"{'🟢' if action == 'BUY' else '🔴'} {action} {symbol} × {qty}\n"
            f"Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | T: ₹{target:.2f}\n"
            f"R:R = {rr:.1f}x | Confidence: {confidence:.0%}"
        )
    except Exception as exc:
        logger.debug("[Engine] Telegram notify error: %s", exc)


# ── Loop 2: Position Monitor ───────────────────────────────────────────────────

def _monitor_loop() -> None:
    logger.info("[Monitor] Loop started")
    trailing_stops: dict[str, float] = {}

    while True:
        try:
            if not _is_monitor_time():
                time.sleep(60)
                continue
            _check_positions(trailing_stops)
        except Exception as exc:
            logger.error("[Monitor] Loop error: %s", exc)

        time.sleep(MONITOR_INTERVAL_SEC)


def _check_positions(trailing_stops: dict[str, float]) -> None:
    from local_db import close_trade, broadcast_event

    broker = _get_broker()

    # Paper broker returns positions from _PAPER_POSITIONS
    # KotakBroker returns positions from API
    try:
        positions = broker.get_positions()
    except Exception as exc:
        logger.debug("[Monitor] get_positions error: %s", exc)
        return

    if not positions:
        return

    mis_squareoff = _is_mis_squareoff_time()

    for pos in positions:
        if not isinstance(pos, dict):
            continue

        # Normalise field names (paper broker vs Kotak API differ)
        sym_raw  = (pos.get("symbol") or pos.get("trdSym") or pos.get("tradingSymbol") or "")
        symbol   = sym_raw.replace("-EQ", "").upper()
        qty_raw  = int(pos.get("qty") or pos.get("flBuyQty") or pos.get("netQty") or 0)
        product  = (pos.get("product") or pos.get("prd") or "MIS").upper()
        avg_price = float(pos.get("entry_price") or pos.get("avgPrice") or pos.get("average_price") or 0)
        action    = (pos.get("action") or "BUY").upper()

        if qty_raw == 0 or not symbol:
            continue

        # Force MIS square-off
        if product == "MIS" and mis_squareoff:
            logger.info("[Monitor] MIS square-off: %s qty=%d", symbol, qty_raw)
            ltp = broker.get_ltp(symbol) or avg_price

            if settings.paper_trading and hasattr(broker, "close_position"):
                broker.close_position(symbol, ltp)
            else:
                broker.place_order(symbol, "SELL", abs(qty_raw), 0, "MKT", "MIS", tag="AUTO_SQUAREOFF")

            pnl = (ltp - avg_price) * abs(qty_raw) if action == "BUY" else (avg_price - ltp) * abs(qty_raw)
            order_id = pos.get("order_id") or pos.get("sl_order_id") or ""
            if order_id:
                try:
                    close_trade(order_id, ltp, round(pnl, 2))
                except Exception:
                    pass
            _send_exit_alert(symbol, pnl, "MIS square-off 15:10 IST")
            broadcast_event("position_closed", {"symbol": symbol, "pnl": round(pnl, 2), "reason": "squareoff"})
            trailing_stops.pop(symbol, None)
            continue

        # Get live price
        ltp = broker.get_ltp(symbol)
        if ltp is None:
            continue

        # Load signal for target and initial stop
        sig_path = SIGNALS_DIR / f"{symbol}_signal.json"
        sig_data: dict = {}
        if sig_path.exists():
            try:
                sig_data = json.loads(sig_path.read_text())
            except Exception:
                pass

        target_price = sig_data.get("target") or (avg_price * 1.05)
        initial_sl   = sig_data.get("stop_loss") or pos.get("stop_loss") or (avg_price * 0.97)
        if not initial_sl or initial_sl == 0:
            initial_sl = avg_price * 0.97

        # Load ATR for trailing stop
        atr = avg_price * 0.015
        cache = Path(f"data/market/{symbol}_ohlcv.json")
        if cache.exists():
            try:
                ohlcv = json.loads(cache.read_text())
                atr = ohlcv.get("atr") or atr
            except Exception:
                pass

        # Update trailing stop
        current_sl = trailing_stops.get(symbol, initial_sl)
        if action == "BUY" and ltp > avg_price:
            new_trail = round(ltp - TRAILING_STOP_ATR * atr, 2)
            if new_trail > current_sl:
                trailing_stops[symbol] = new_trail
                current_sl = new_trail
        elif action == "SELL" and ltp < avg_price:
            new_trail = round(ltp + TRAILING_STOP_ATR * atr, 2)
            if new_trail < current_sl:
                trailing_stops[symbol] = new_trail
                current_sl = new_trail

        exit_reason: str | None = None
        if action == "BUY":
            if ltp >= target_price:
                exit_reason = f"TARGET HIT ₹{ltp:.2f} ≥ ₹{target_price:.2f}"
            elif ltp <= current_sl:
                exit_reason = f"STOP HIT ₹{ltp:.2f} ≤ ₹{current_sl:.2f}"
        else:  # SELL / short
            if ltp <= target_price:
                exit_reason = f"TARGET HIT ₹{ltp:.2f} ≤ ₹{target_price:.2f}"
            elif ltp >= current_sl:
                exit_reason = f"STOP HIT ₹{ltp:.2f} ≥ ₹{current_sl:.2f}"

        if exit_reason:
            logger.info("[Monitor] Exit triggered %s: %s", symbol, exit_reason)
            pnl = (ltp - avg_price) * abs(qty_raw) if action == "BUY" else (avg_price - ltp) * abs(qty_raw)

            if settings.paper_trading and hasattr(broker, "close_position"):
                broker.close_position(symbol, ltp)
            else:
                broker.place_order(symbol, "SELL" if action == "BUY" else "BUY",
                                   abs(qty_raw), 0, "MKT", product, tag="AUTO_EXIT")

            order_id = pos.get("order_id", "")
            if order_id:
                try:
                    close_trade(order_id, ltp, round(pnl, 2))
                except Exception:
                    pass

            broadcast_event("position_closed", {
                "symbol": symbol, "pnl": round(pnl, 2), "reason": exit_reason,
                "ltp": ltp,
            })
            _send_exit_alert(symbol, pnl, exit_reason)
            trailing_stops.pop(symbol, None)
            logger.info("[Monitor] Exit done %s P&L=₹%.0f reason=%s", symbol, pnl, exit_reason)


def _send_exit_alert(symbol: str, pnl: float, reason: str) -> None:
    try:
        from monitoring.telegram_bot import send_text
        sign = "✅" if pnl >= 0 else "❌"
        send_text(
            f"{sign} EXIT: {symbol}\n"
            f"P&L: ₹{pnl:+.0f}\n"
            f"Reason: {reason}"
            + ("\n[PAPER]" if settings.paper_trading else "")
        )
    except Exception as exc:
        logger.debug("[Monitor] Telegram alert error: %s", exc)


# ── Loop 3: EOD Cleanup ────────────────────────────────────────────────────────

def _eod_loop() -> None:
    logger.info("[EOD] Loop started")
    eod_done_date: str = ""

    while True:
        try:
            now   = _ist_now()
            today = now.strftime("%Y-%m-%d")
            h, m  = now.hour, now.minute

            if (h, m) >= EOD_TIME and today != eod_done_date:
                logger.info("[EOD] Running cleanup…")
                _run_eod()
                eod_done_date = today

        except Exception as exc:
            logger.error("[EOD] Loop error: %s", exc)

        time.sleep(300)


def _run_eod() -> None:
    try:
        from monitoring.pnl_tracker import run_eod
        _asyncio.run(run_eod())
        logger.info("[EOD] P&L tracker completed")
    except Exception as exc:
        logger.warning("[EOD] P&L tracker error: %s", exc)

    # Broadcast EOD to frontend
    try:
        from local_db import broadcast_event
        broadcast_event("eod", {"date": _ist_now().strftime("%Y-%m-%d")})
    except Exception:
        pass


# ── Engine start ───────────────────────────────────────────────────────────────

_engine_started = False
_engine_lock = threading.Lock()


def start_engine() -> None:
    """Start all three loops + realtime data collector as daemon threads."""
    global _engine_started
    with _engine_lock:
        if _engine_started:
            return
        _engine_started = True

    mode = "PAPER" if settings.paper_trading else "LIVE"
    logger.info("[Engine] Starting in %s mode", mode)

    # Start realtime Kotak data collector
    try:
        from data_collector.kotak_realtime import get_collector
        collector = get_collector()
        # Pre-load any existing picks
        initial_stocks = _get_scan_symbols()
        collector.start(initial_stocks)
        logger.info("[Engine] Realtime collector started for %d symbols", len(initial_stocks))
    except Exception as exc:
        logger.warning("[Engine] Could not start realtime collector: %s", exc)

    threads = [
        threading.Thread(target=_scan_loop,    name="scan-loop",    daemon=True),
        threading.Thread(target=_monitor_loop, name="monitor-loop", daemon=True),
        threading.Thread(target=_eod_loop,     name="eod-loop",     daemon=True),
    ]
    for t in threads:
        t.start()
        logger.info("[Engine] Started thread: %s", t.name)

    logger.info("[Engine] Trade engine running — 3 loops active [%s]", mode)


# ── Standalone entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import time as _time
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Start Telegram bot for alerts only (no approval flow)
    try:
        from monitoring.telegram_bot import start_bot_thread
        start_bot_thread()
    except Exception as e:
        logger.warning("Could not start Telegram bot: %s", e)

    start_engine()
    paper_note = " [PAPER TRADING]" if settings.paper_trading else " [LIVE TRADING]"
    logger.info("Trade engine running standalone%s. Press Ctrl+C to stop.", paper_note)

    try:
        while True:
            _time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down trade engine.")
        sys.exit(0)
