"""
Kotak Realtime Data Collector — 1-minute live OHLCV from Kotak Neo.

Polls Kotak live quotes every 60 seconds during NSE market hours.
Builds 1-minute OHLCV bars and writes:
  - data/realtime/{symbol}_1m.json   (rolling 390-bar session buffer)
  - data/market/{symbol}_ohlcv.json  (strategy-engine-compatible format)

Also computes intraday indicators needed by the new strategies:
  EMA-9, EMA-21, VWAP, ATR-14, RSI-14,
  or_high, or_low (opening range 9:15–9:30),
  prev_day_high/low/close, session_open.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings

logger = logging.getLogger(__name__)

IST        = ZoneInfo("Asia/Kolkata")
REALTIME_DIR = Path("data/realtime")
MARKET_DIR   = Path("data/market")
REALTIME_DIR.mkdir(parents=True, exist_ok=True)
MARKET_DIR.mkdir(parents=True, exist_ok=True)

_MAX_BARS     = 390    # full 6.5-hour session in 1-min bars
_POLL_SEC     = 60     # poll every 60 seconds
_OR_END_MIN   = 15     # opening range = first 15 minutes (9:15–9:30 AM)
_MARKET_OPEN  = (9, 15)
_MARKET_CLOSE = (15, 30)


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _ema_update(prev_ema: float, new_val: float, period: int) -> float:
    k = 2.0 / (period + 1)
    return new_val * k + prev_ema * (1 - k)


def _compute_vwap(bars: list[dict]) -> float:
    num = sum(((b["h"] + b["l"] + b["c"]) / 3) * b["v"] for b in bars)
    den = sum(b["v"] for b in bars)
    return round(num / den, 2) if den > 0 else 0.0


def _compute_atr(bars: list[dict], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    # EMA of TR
    val = sum(trs[:period]) / min(len(trs), period)
    for tr in trs[period:]:
        val = _ema_update(val, tr, period)
    return round(val, 4)


def _compute_rsi(bars: list[dict], period: int = 14) -> float:
    closes = [b["c"] for b in bars]
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = _ema_update(avg_g, g, period)
        avg_l = _ema_update(avg_l, l, period)
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)


def _compute_macd(bars: list[dict], fast=12, slow=26, sig=9) -> tuple[float, float, float]:
    closes = [b["c"] for b in bars]
    if len(closes) < slow + sig:
        return 0.0, 0.0, 0.0

    def ema_series(vals, period):
        result = [sum(vals[:period]) / period]
        for v in vals[period:]:
            result.append(_ema_update(result[-1], v, period))
        return result

    fast_e = ema_series(closes, fast)
    slow_e = ema_series(closes, slow)
    # Align: slow starts at index slow-1, fast starts at fast-1
    # macd line needs both, so start from max(fast, slow) offset
    offset = slow - fast
    macd_line = [f - s for f, s in zip(fast_e[offset:], slow_e)]
    if len(macd_line) < sig:
        return 0.0, 0.0, 0.0
    sig_line = ema_series(macd_line, sig)
    m = macd_line[-1]
    s = sig_line[-1]
    return round(m, 4), round(s, 4), round(m - s, 4)


class SymbolTracker:
    """Tracks 1-minute OHLCV bars for a single symbol."""

    def __init__(self, symbol: str) -> None:
        self.symbol    = symbol
        self.bars: deque[dict] = deque(maxlen=_MAX_BARS)
        self._cur_bar: dict | None = None
        self._cur_min: str = ""
        self.or_high: float | None = None
        self.or_low:  float | None = None
        self.session_open: float | None = None
        self.prev_day_high:  float | None = None
        self.prev_day_low:   float | None = None
        self.prev_day_close: float | None = None
        self._ema9:  float | None = None
        self._ema21: float | None = None
        self._load_prev_day()

    def _load_prev_day(self) -> None:
        """Load previous day's H/L/C from daily cache for CPR strategy."""
        try:
            path = MARKET_DIR / f"{self.symbol}_ohlcv.json"
            if path.exists():
                data = json.loads(path.read_text())
                candles = data.get("candles", [])
                if len(candles) >= 2:
                    prev = candles[-2]   # second-to-last daily bar
                    self.prev_day_high  = prev["h"]
                    self.prev_day_low   = prev["l"]
                    self.prev_day_close = prev["c"]
                    logger.debug("[Realtime] %s prev_day: H=%.2f L=%.2f C=%.2f",
                                 self.symbol, prev["h"], prev["l"], prev["c"])
        except Exception as exc:
            logger.debug("[Realtime] Could not load prev day for %s: %s", self.symbol, exc)

    def tick(self, ltp: float, ohlc: dict | None, volume: int, ts: datetime) -> None:
        """Process a new quote tick."""
        minute_key = ts.strftime("%Y-%m-%dT%H:%M")
        h = ohlc.get("high", ltp) if ohlc else ltp
        l = ohlc.get("low", ltp)  if ohlc else ltp
        o = ohlc.get("open", ltp) if ohlc else ltp

        if minute_key != self._cur_min:
            # Close the previous bar
            if self._cur_bar:
                self.bars.append(self._cur_bar)
                self._finalize_bar(self._cur_bar)
            # Open a new bar
            self._cur_bar = {"t": minute_key, "o": o, "h": h, "l": l, "c": ltp, "v": volume}
            self._cur_min = minute_key

            # Set session open (first tick of the day)
            if self.session_open is None:
                self.session_open = o

            # Track opening range (first 15 min = 9:15–9:30)
            session_elapsed_min = (ts.hour - _MARKET_OPEN[0]) * 60 + (ts.minute - _MARKET_OPEN[1])
            if session_elapsed_min < _OR_END_MIN:
                self.or_high = max(self.or_high or h, h)
                self.or_low  = min(self.or_low  or l, l)
        else:
            # Update current bar
            if self._cur_bar:
                self._cur_bar["h"] = max(self._cur_bar["h"], h)
                self._cur_bar["l"] = min(self._cur_bar["l"], l)
                self._cur_bar["c"] = ltp
                self._cur_bar["v"] = volume

    def _finalize_bar(self, bar: dict) -> None:
        """Update rolling indicators after a bar is closed."""
        close = bar["c"]
        if self._ema9 is None:
            self._ema9  = close
            self._ema21 = close
        else:
            self._ema9  = _ema_update(self._ema9, close, 9)
            self._ema21 = _ema_update(self._ema21, close, 21)

    def to_ohlcv_payload(self) -> dict:
        """Build strategy-engine-compatible ohlcv dict from accumulated bars."""
        bars = list(self.bars)
        if self._cur_bar:
            bars = bars + [self._cur_bar]   # include in-progress bar

        if not bars:
            return {}

        last    = bars[-1]
        closes  = [b["c"] for b in bars]
        last_close = closes[-1]

        vwap = _compute_vwap(bars)
        atr  = _compute_atr(bars, period=14)
        rsi  = _compute_rsi(bars, period=14)
        macd_line, macd_sig, macd_hist = _compute_macd(bars)
        prev_hist = _compute_macd(bars[:-1])[2] if len(bars) > 1 else 0.0

        ema9  = self._ema9  or last_close
        ema21 = self._ema21 or last_close
        bb_closes = closes[-20:]
        bb_mean = sum(bb_closes) / len(bb_closes)
        bb_std  = math.sqrt(sum((c - bb_mean) ** 2 for c in bb_closes) / len(bb_closes)) if len(bb_closes) > 1 else atr
        bb_upper = bb_mean + 2 * bb_std
        bb_lower = bb_mean - 2 * bb_std
        bb_pct   = (last_close - bb_lower) / max(bb_upper - bb_lower, 0.0001)

        vol_today = sum(b["v"] for b in bars[-20:])
        vol_prev  = sum(b["v"] for b in bars[-40:-20]) if len(bars) >= 40 else vol_today
        vol_ratio = round(vol_today / max(vol_prev, 1), 2)

        payload = {
            "symbol":         self.symbol,
            "exchange":       "NSE",
            "timestamp":      datetime.now(IST).isoformat(),
            "interval":       "1m",
            "last_close":     round(last_close, 2),
            "rsi":            rsi,
            "macd":           macd_line,
            "macd_signal":    macd_sig,
            "macd_hist":      macd_hist,
            "macd_crossover": macd_hist > 0 and prev_hist <= 0,
            "macd_crossunder": macd_hist < 0 and prev_hist >= 0,
            "bb_upper":       round(bb_upper, 2),
            "bb_mid":         round(bb_mean, 2),
            "bb_lower":       round(bb_lower, 2),
            "bb_pct":         round(bb_pct, 4),
            "ema9":           round(ema9, 2),
            "ema21":          round(ema21, 2),
            "ema20":          round(ema21, 2),   # alias for strategy compat
            "above_ema20":    last_close > ema21,
            "above_ema200":   last_close > ema21,  # simplified; use daily file for accurate EMA-200
            "atr":            atr,
            "vol_ratio":      vol_ratio,
            "vwap":           vwap,
            # Intraday-specific fields for new strategies
            "or_high":        round(self.or_high, 2) if self.or_high else None,
            "or_low":         round(self.or_low, 2)  if self.or_low  else None,
            "session_open":   round(self.session_open, 2) if self.session_open else None,
            "prev_day_high":  self.prev_day_high,
            "prev_day_low":   self.prev_day_low,
            "prev_day_close": self.prev_day_close,
            # Chart candles (last 390 bars for frontend)
            "candles": [
                {"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
                for b in bars
            ],
        }
        return payload

    def save(self) -> None:
        payload = self.to_ohlcv_payload()
        if not payload:
            return
        # Rolling session buffer
        realtime_path = REALTIME_DIR / f"{self.symbol}_1m.json"
        realtime_path.write_text(json.dumps(payload, indent=2))
        # Strategy-engine-compatible file
        market_path = MARKET_DIR / f"{self.symbol}_ohlcv.json"
        market_path.write_text(json.dumps(payload, indent=2))


class KotakRealtimeCollector:
    """
    Background thread that polls Kotak live quotes every 60 seconds
    and maintains per-symbol 1-minute OHLCV bars.
    """

    def __init__(self) -> None:
        self._trackers: dict[str, SymbolTracker] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._broker = None
        self._symbols: list[str] = []

    def _get_broker(self):
        if self._broker is None:
            try:
                if settings.paper_trading:
                    from broker.paper_broker import PaperBroker
                    self._broker = PaperBroker()
                else:
                    from broker.kotak_direct import KotakBroker
                    self._broker = KotakBroker()
            except Exception as exc:
                logger.error("[Realtime] Could not init broker: %s", exc)
        return self._broker

    def _is_market_open(self) -> bool:
        now = datetime.now(IST)
        if now.weekday() >= 5:   # Saturday/Sunday
            return False
        start = now.replace(hour=_MARKET_OPEN[0],  minute=_MARKET_OPEN[1],  second=0, microsecond=0)
        end   = now.replace(hour=_MARKET_CLOSE[0], minute=_MARKET_CLOSE[1], second=0, microsecond=0)
        return start <= now <= end

    def update_symbols(self, symbols: list[str]) -> None:
        """Called by trade engine when daily stock list changes."""
        self._symbols = symbols
        # Create trackers for new symbols
        for sym in symbols:
            if sym not in self._trackers:
                self._trackers[sym] = SymbolTracker(sym)

    def _poll_once(self) -> None:
        """One polling cycle: fetch quotes for all symbols and update bars."""
        broker = self._get_broker()
        if not broker:
            return

        now = datetime.now(IST)

        for sym in list(self._symbols):
            try:
                quote_list = broker.get_quote(sym)
                if not quote_list:
                    continue

                q = quote_list[0] if isinstance(quote_list, list) else quote_list
                ltp    = float(q.get("ltp") or q.get("last_price") or q.get("lastPrice") or 0)
                volume = int(q.get("totalVolume") or q.get("volume") or q.get("Volume") or 0)
                ohlc   = {
                    "open":  float(q.get("open") or ltp),
                    "high":  float(q.get("dayHigh") or q.get("high") or ltp),
                    "low":   float(q.get("dayLow") or q.get("low") or ltp),
                }

                if ltp <= 0:
                    continue

                tracker = self._trackers.setdefault(sym, SymbolTracker(sym))
                tracker.tick(ltp, ohlc, volume, now)
                tracker.save()

            except Exception as exc:
                logger.debug("[Realtime] %s poll error: %s", sym, exc)

    def _run(self) -> None:
        logger.info("[Realtime] Collector started — polling every %ds", _POLL_SEC)
        while not self._stop_event.is_set():
            if self._is_market_open() and self._symbols:
                try:
                    self._poll_once()
                except Exception as exc:
                    logger.error("[Realtime] Poll cycle error: %s", exc)
            self._stop_event.wait(timeout=_POLL_SEC)
        logger.info("[Realtime] Collector stopped")

    def start(self, symbols: list[str] | None = None) -> None:
        if symbols:
            self.update_symbols(symbols)
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="KotakRealtime", daemon=True)
        self._thread.start()
        logger.info("[Realtime] Thread started for %d symbols", len(self._symbols))

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def get_latest(self, symbol: str) -> dict:
        """Return the latest ohlcv payload for a symbol (for direct use by strategies)."""
        tracker = self._trackers.get(symbol)
        if tracker:
            return tracker.to_ohlcv_payload()
        # Fallback: read from file
        path = MARKET_DIR / f"{symbol}_ohlcv.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}


# Module-level singleton used by trade engine
_collector: KotakRealtimeCollector | None = None


def get_collector() -> KotakRealtimeCollector:
    global _collector
    if _collector is None:
        _collector = KotakRealtimeCollector()
    return _collector
