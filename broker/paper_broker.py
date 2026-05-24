"""
Paper Trading Broker — drop-in replacement for KotakBroker.

Simulates all order operations locally; delegates live quote/LTP calls to
KotakBroker so virtual P&L reflects real market prices. All paper trades
are tagged PAPER and stored in the local SQLite DB via local_db.

Interface is identical to KotakBroker — trade engine just swaps the instance.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_PAPER_POSITIONS: dict[str, dict] = {}   # symbol → position dict (in-memory)
_ORDER_COUNTER = 0


def _next_order_id() -> str:
    global _ORDER_COUNTER
    _ORDER_COUNTER += 1
    return f"PAPER_{int(time.time())}_{_ORDER_COUNTER:04d}"


class PaperBroker:
    """
    Simulates broker operations for paper trading.
    Live market prices are fetched via a real KotakBroker instance so
    mark-to-market P&L uses actual prices.
    """

    def __init__(self) -> None:
        # Lazy-load KotakBroker only for market data (quotes/LTP), never for orders
        self._real_broker = None
        logger.info("[PAPER] PaperBroker initialised — no real orders will be placed")

    def _live(self):
        if self._real_broker is None:
            try:
                from broker.kotak_direct import KotakBroker
                self._real_broker = KotakBroker()
            except Exception as exc:
                logger.warning("[PAPER] Could not init KotakBroker for live quotes: %s", exc)
        return self._real_broker

    # ── Order execution (simulated) ────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        action: str,
        qty: int,
        price: float,
        order_type: str = "L",
        product: str = "MIS",
        tag: str = "PAPER",
        trigger_price: float = 0.0,
        exchange: str = "NSE",
    ) -> dict:
        order_id = _next_order_id()
        ltp = self.get_ltp(symbol) or price
        fill_price = ltp   # simulated fill at current market price

        pos = {
            "order_id": order_id,
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "entry_price": fill_price,
            "product": product,
            "tag": "PAPER",
            "is_open": True,
            "paper": True,
            "placed_at": datetime.now(tz=timezone.utc).isoformat(),
            "stop_loss": None,
            "target": None,
            "sl_order_id": None,
        }
        _PAPER_POSITIONS[symbol] = pos

        try:
            import local_db
            local_db.record_trade({
                "order_id": order_id,
                "symbol": symbol,
                "tier": "EQUITY",
                "action": action,
                "product": product,
                "qty": qty,
                "entry_price": fill_price,
                "stop_loss": 0.0,
                "target": 0.0,
                "is_open": True,
                "tag": "PAPER",
                "reasoning": f"Paper trade — simulated fill at {fill_price}",
            })
        except Exception as exc:
            logger.warning("[PAPER] Could not record trade in local_db: %s", exc)

        logger.info("[PAPER] %s %s %d @ %.2f (order_id=%s)", action, symbol, qty, fill_price, order_id)
        return {"order_id": order_id, "status": "complete", "fill_price": fill_price, "paper": True}

    def place_stop_loss(
        self,
        symbol: str,
        action: str,
        qty: int,
        trigger_price: float,
        product: str = "MIS",
    ) -> dict:
        sl_order_id = _next_order_id()
        if symbol in _PAPER_POSITIONS:
            _PAPER_POSITIONS[symbol]["stop_loss"] = trigger_price
            _PAPER_POSITIONS[symbol]["sl_order_id"] = sl_order_id

        logger.info("[PAPER] SL set: %s %s trigger=%.2f (sl_order_id=%s)",
                    symbol, action, trigger_price, sl_order_id)
        return {"order_id": sl_order_id, "status": "complete", "paper": True}

    def cancel_order(self, order_id: str) -> dict:
        logger.info("[PAPER] Cancel order %s (simulated)", order_id)
        return {"order_id": order_id, "status": "cancelled", "paper": True}

    def modify_order(
        self,
        order_id: str,
        price: float,
        qty: int,
        order_type: str = "SL-M",
        validity: str = "DAY",
        trigger_price: float = 0.0,
    ) -> dict:
        # Update SL in in-memory position if this is a trailing SL
        for sym, pos in _PAPER_POSITIONS.items():
            if pos.get("sl_order_id") == order_id:
                pos["stop_loss"] = trigger_price
                logger.info("[PAPER] Trailing SL updated: %s → %.2f", sym, trigger_price)
                break
        return {"order_id": order_id, "status": "modified", "paper": True}

    def get_order_status(self, order_id: str) -> str:
        return "complete"

    def extract_order_id(self, result: dict) -> str | None:
        return result.get("order_id")

    # ── Portfolio queries ──────────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Return open paper positions with live mark-to-market P&L."""
        positions = []
        for sym, pos in list(_PAPER_POSITIONS.items()):
            if not pos.get("is_open"):
                continue
            ltp = self.get_ltp(sym)
            if ltp:
                qty = pos["qty"]
                entry = pos["entry_price"]
                if pos["action"] == "BUY":
                    unrealised = (ltp - entry) * qty
                else:
                    unrealised = (entry - ltp) * qty
                pos_copy = dict(pos, ltp=ltp, unrealised_pnl=round(unrealised, 2))
            else:
                pos_copy = dict(pos, ltp=pos["entry_price"], unrealised_pnl=0.0)
            positions.append(pos_copy)
        return positions

    def get_open_position(self, symbol: str) -> dict | None:
        pos = _PAPER_POSITIONS.get(symbol)
        if pos and pos.get("is_open"):
            return pos
        return None

    def get_holdings(self) -> list[dict]:
        return []

    def get_order_book(self) -> list[dict]:
        return list(_PAPER_POSITIONS.values())

    def get_account_equity(self) -> float:
        return 500_000.0   # simulated 5L capital

    def get_limits(self, segment: str = "equity", exchange: str = "nse_cm", product: str = "MIS") -> dict:
        open_positions = self.get_positions()
        used = sum(p.get("entry_price", 0) * p.get("qty", 0) for p in open_positions)
        return {
            "cash_available": max(0, 500_000 - used),
            "net_available": max(0, 500_000 - used),
            "paper": True,
        }

    # ── Market data — delegate to real broker ──────────────────────────────────

    def get_quote(self, symbol: str, exchange: str = "nse_cm") -> list | dict:
        broker = self._live()
        if broker:
            try:
                return broker.get_quote(symbol, exchange)
            except Exception as exc:
                logger.debug("[PAPER] get_quote(%s) failed: %s", symbol, exc)
        return []

    def get_ltp(self, symbol: str) -> float | None:
        broker = self._live()
        if broker:
            try:
                return broker.get_ltp(symbol)
            except Exception as exc:
                logger.debug("[PAPER] get_ltp(%s) failed: %s", symbol, exc)
        # Fallback: read from cached market data
        try:
            import json
            path = Path(f"data/market/{symbol}_ohlcv.json")
            if path.exists():
                data = json.loads(path.read_text())
                return data.get("last_close")
        except Exception:
            pass
        return None

    def search_scrip(self, exchange: str, symbol: str) -> list[dict]:
        broker = self._live()
        if broker:
            try:
                return broker.search_scrip(exchange, symbol)
            except Exception:
                pass
        return []

    # ── Paper-only helpers ─────────────────────────────────────────────────────

    def close_position(self, symbol: str, exit_price: float | None = None) -> dict | None:
        """Simulate closing a paper position."""
        pos = _PAPER_POSITIONS.get(symbol)
        if not pos or not pos.get("is_open"):
            return None

        ltp = exit_price or self.get_ltp(symbol) or pos["entry_price"]
        qty = pos["qty"]
        entry = pos["entry_price"]

        if pos["action"] == "BUY":
            pnl = (ltp - entry) * qty
        else:
            pnl = (entry - ltp) * qty

        pos["is_open"] = False
        pos["exit_price"] = ltp
        pos["realized_pnl"] = round(pnl, 2)

        try:
            import local_db
            local_db.close_trade(pos["order_id"], ltp, round(pnl, 2))
        except Exception as exc:
            logger.warning("[PAPER] Could not close trade in local_db: %s", exc)

        logger.info("[PAPER] Closed %s: entry=%.2f exit=%.2f P&L=%.2f",
                    symbol, entry, ltp, pnl)
        return pos
