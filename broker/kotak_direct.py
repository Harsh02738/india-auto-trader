"""
Direct Kotak Neo API wrapper — callable from Python background services
without going through the MCP protocol.

Usage:
    from broker.kotak_direct import KotakBroker
    broker = KotakBroker()
    quote = broker.get_quote("RELIANCE")
    order = broker.place_order("RELIANCE", "BUY", 10, 2845.0, "L", "MIS")
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pyotp

from config.settings import settings

logger = logging.getLogger(__name__)

SESSION_FILE = Path("data/portfolio/session.json")
SESSION_TTL = 23 * 3600  # re-auth after 23 hours


class KotakBroker:
    """
    Thread-safe wrapper around neo_api_client.NeoAPI.
    Handles authentication, session renewal, and retries.
    """

    def __init__(self) -> None:
        self._client = None
        self._auth_ts: float = 0

    # ── Authentication ─────────────────────────────────────────────────────────

    def _get_client(self):
        if self._client is not None and (time.time() - self._auth_ts) < SESSION_TTL:
            return self._client
        return self._authenticate()

    def _authenticate(self):
        from neo_api_client import NeoAPI

        client = NeoAPI(
            consumer_key=settings.kotak_consumer_key,
            consumer_secret=settings.kotak_consumer_secret,
            environment=settings.kotak_environment,
            on_message=lambda msg: logger.debug("WS tick: %s", msg),
            on_error=lambda msg: logger.error("WS error: %s", msg),
            on_open=lambda msg: logger.info("WS opened"),
            on_close=lambda msg: logger.info("WS closed"),
        )

        totp = pyotp.TOTP(settings.kotak_totp_secret).now()
        client.login(mobilenumber=settings.kotak_mobile_number, password=settings.kotak_mpin)
        client.session_2fa(OTP=totp)

        self._client = client
        self._auth_ts = time.time()
        logger.info("Kotak Neo authenticated (env=%s)", settings.kotak_environment)

        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps({"auth_ts": self._auth_ts, "env": settings.kotak_environment}))
        return client

    def _call(self, fn, *args, **kwargs) -> dict | list:
        """Execute a Neo API call with retry on rate limit."""
        for attempt in range(3):
            try:
                result = fn(*args, **kwargs)
                return result if isinstance(result, (dict, list)) else {"result": result}
            except Exception as exc:
                msg = str(exc)
                if "429" in msg and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                logger.error("Kotak API error: %s", exc)
                return {"error": msg, "status": "failed"}
        return {"error": "max retries exceeded", "status": "failed"}

    # ── Market Data ────────────────────────────────────────────────────────────

    def get_quote(self, symbol: str, exchange: str = "nse_cm") -> dict:
        """Real-time LTP, OHLC, and volume for a symbol."""
        client = self._get_client()
        scrip = self._call(client.search_scrip, exchange_segment=exchange, symbol=symbol)
        tokens = scrip if isinstance(scrip, list) else scrip.get("data", [])
        if not tokens:
            return {"error": f"Symbol {symbol} not found on {exchange}"}
        token = tokens[0].get("pSymbol") or tokens[0].get("instrumentToken")
        return self._call(
            client.quotes,
            instrument_tokens=[{"instrument_token": str(token), "exchange_segment": exchange}],
            quote_type="all",
        )

    def get_ltp(self, symbol: str, exchange: str = "nse_cm") -> float | None:
        """Return the last traded price as a float, or None on error."""
        quote = self.get_quote(symbol, exchange)
        if "error" in quote:
            return None
        data = quote if isinstance(quote, dict) else {}
        # neo_api_client returns different shapes; try common keys
        for key in ("ltp", "lastPrice", "last_price", "close"):
            val = data.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        # Nested under data[]
        items = data.get("data", [])
        if items and isinstance(items, list):
            row = items[0]
            for key in ("ltp", "lastPrice", "last_price", "close"):
                val = row.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        pass
        return None

    # ── Portfolio ──────────────────────────────────────────────────────────────

    def get_positions(self) -> list:
        """All open intraday (MIS) and overnight (CNC/NRML) positions."""
        result = self._call(self._get_client().positions)
        return result if isinstance(result, list) else result.get("data", [])

    def get_holdings(self) -> list:
        """Delivery holdings (T+2 settled)."""
        result = self._call(self._get_client().holdings, "")
        return result if isinstance(result, list) else result.get("data", [])

    def get_order_book(self) -> list:
        """All orders placed today."""
        result = self._call(self._get_client().order_report)
        return result if isinstance(result, list) else result.get("data", [])

    def get_limits(self, segment: str = "ALL", exchange: str = "ALL", product: str = "ALL") -> dict:
        """Available cash, margin, and utilization."""
        return self._call(
            self._get_client().limits,
            segment=segment,
            exchange=exchange,
            product=product,
        )

    def get_account_equity(self) -> float:
        """Total account value from limits API. Falls back to 500,000 if unavailable."""
        limits = self.get_limits()
        for key in ("net", "total", "available", "availablecash", "availableMargin"):
            val = limits.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        data = limits.get("data", {})
        if isinstance(data, dict):
            for key in ("net", "total", "available"):
                val = data.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        pass
        logger.warning("Could not parse account equity from limits; using 500000")
        return 500_000.0

    # ── Order Execution ────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        action: str,
        qty: int,
        price: float,
        order_type: str,
        product: str,
        trigger_price: float = 0,
        tag: str = "AUTO",
        exchange: str = "nse_cm",
    ) -> dict:
        """
        Place an equity order.
        action: BUY or SELL
        order_type: L (limit), MKT, SL, SL-M
        product: MIS (intraday) or CNC (delivery)
        Returns dict with order_id on success.
        """
        client = self._get_client()
        trading_symbol = f"{symbol}-EQ" if exchange in ("nse_cm", "bse_cm") else symbol
        result = self._call(
            client.place_order,
            exchange_segment=exchange,
            product=product,
            price=str(price) if order_type == "L" else "0",
            order_type=order_type,
            quantity=str(qty),
            validity="DAY",
            trading_symbol=trading_symbol,
            transaction_type="B" if action.upper() == "BUY" else "S",
            amo="NO",
            disclosed_quantity="0",
            market_protection="0",
            pf="N",
            trigger_price=str(trigger_price) if trigger_price else "0",
            tag=tag,
        )
        if "error" not in result:
            logger.info("Order placed: %s %s qty=%d @%.2f [%s]", action, symbol, qty, price, result)
        return result

    def place_stop_loss(
        self,
        symbol: str,
        action: str,
        qty: int,
        trigger_price: float,
        product: str,
        exchange: str = "nse_cm",
    ) -> dict:
        """Place a SL-M stop-loss order immediately after entry."""
        return self.place_order(
            symbol=symbol,
            action=action,
            qty=qty,
            price=0,
            order_type="SL-M",
            product=product,
            trigger_price=trigger_price,
            tag="AUTO_SL",
            exchange=exchange,
        )

    def cancel_order(self, order_id: str) -> dict:
        """Cancel a pending order."""
        return self._call(self._get_client().cancel_order, order_id=order_id)

    def modify_order(
        self,
        order_id: str,
        price: float,
        qty: int,
        order_type: str,
        validity: str = "DAY",
        trigger_price: float = 0,
    ) -> dict:
        """Modify a pending order (e.g. trail stop-loss)."""
        return self._call(
            self._get_client().modify_order,
            order_id=order_id,
            price=str(price),
            quantity=str(qty),
            order_type=order_type,
            validity=validity,
            trigger_price=str(trigger_price) if trigger_price else "0",
        )

    def get_order_status(self, order_id: str) -> str:
        """Return order status string: 'complete', 'open', 'rejected', etc."""
        orders = self.get_order_book()
        for order in orders:
            if isinstance(order, dict):
                oid = order.get("nOrdNo") or order.get("orderId") or order.get("order_id")
                if str(oid) == str(order_id):
                    return str(order.get("status") or order.get("orderStatus") or "unknown").lower()
        return "not_found"

    def extract_order_id(self, place_result: dict) -> str | None:
        """Extract order ID from place_order response."""
        if "error" in place_result:
            return None
        for key in ("nOrdNo", "orderId", "order_id"):
            val = place_result.get(key)
            if val:
                return str(val)
        return None

    # ── Position helpers ───────────────────────────────────────────────────────

    def get_open_position(self, symbol: str) -> dict | None:
        """Return the open position dict for a symbol, or None."""
        for pos in self.get_positions():
            if not isinstance(pos, dict):
                continue
            pos_sym = pos.get("trdSym") or pos.get("symbol") or pos.get("tradingSymbol") or ""
            clean = pos_sym.replace("-EQ", "").upper()
            if clean == symbol.upper():
                qty = int(pos.get("flBuyQty") or pos.get("netQty") or pos.get("quantity") or 0)
                if qty != 0:
                    return pos
        return None
