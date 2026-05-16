"""
Kotak Neo MCP Server — exposes all Kotak Neo API v2 trading tools to Claude Code.
Claude Code calls these tools directly (no Anthropic API key needed).

Run: python mcp_server/kotak_mcp.py
Register in .claude/settings.json under mcpServers.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import pyotp
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from supabase_client import record_trade, close_trade as sb_close_trade

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kotak_mcp")

mcp = FastMCP("kotak-neo", dependencies=["neo_api_client", "pyotp"])

_client = None
_auth_ts: float = 0
SESSION_TTL = 23 * 3600  # re-auth after 23 hours


def _get_client():
    global _client, _auth_ts
    if _client is not None and (time.time() - _auth_ts) < SESSION_TTL:
        return _client

    from neo_api_client import NeoAPI

    env = os.environ.get("KOTAK_ENVIRONMENT", "prod")
    consumer_key = os.environ["KOTAK_CONSUMER_KEY"]
    consumer_secret = os.environ["KOTAK_CONSUMER_SECRET"]

    client = NeoAPI(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        environment=env,
        on_message=_on_ws_message,
        on_error=_on_ws_error,
        on_open=_on_ws_open,
        on_close=_on_ws_close,
    )

    mobile = os.environ["KOTAK_MOBILE_NUMBER"]
    mpin = os.environ["KOTAK_MPIN"]
    totp_secret = os.environ["KOTAK_TOTP_SECRET"]
    totp = pyotp.TOTP(totp_secret).now()

    client.login(mobilenumber=mobile, password=mpin)
    client.session_2fa(OTP=totp)

    _client = client
    _auth_ts = time.time()
    log.info("Kotak Neo authenticated (env=%s)", env)
    return client


def _on_ws_message(msg): log.debug("WS tick: %s", msg)
def _on_ws_error(msg): log.error("WS error: %s", msg)
def _on_ws_open(msg): log.info("WS opened: %s", msg)
def _on_ws_close(msg): log.info("WS closed: %s", msg)


def _safe_call(fn, *args, **kwargs) -> dict:
    try:
        result = fn(*args, **kwargs)
        return result if isinstance(result, (dict, list)) else {"result": result}
    except Exception as exc:
        log.error("Kotak API error: %s", exc)
        return {"error": str(exc), "status": "failed"}


# ─── PORTFOLIO TOOLS ──────────────────────────────────────────────────────────

@mcp.tool()
def get_limits(segment: str = "ALL", exchange: str = "ALL", product: str = "ALL") -> dict:
    """
    Get account cash limits, available margin, and utilization.
    segment: ALL, CASH, FO, CUR
    exchange: ALL, NSE, BSE
    product: ALL, CNC, MIS, NRML
    """
    return _safe_call(_get_client().limits, segment=segment, exchange=exchange, product=product)


@mcp.tool()
def get_positions() -> list:
    """Get all open intraday (MIS) and overnight (CNC/NRML) positions with unrealized P&L."""
    result = _safe_call(_get_client().positions)
    return result if isinstance(result, list) else result.get("data", [])


@mcp.tool()
def get_holdings() -> list:
    """Get delivery holdings (CNC positions settled T+2)."""
    result = _safe_call(_get_client().holdings, "")
    return result if isinstance(result, list) else result.get("data", [])


@mcp.tool()
def get_order_book() -> list:
    """Get all orders placed today (pending, executed, cancelled, rejected)."""
    result = _safe_call(_get_client().order_report)
    return result if isinstance(result, list) else result.get("data", [])


# ─── MARKET DATA TOOLS ────────────────────────────────────────────────────────

@mcp.tool()
def get_quote(symbol: str, exchange: str = "nse_cm") -> dict:
    """
    Get real-time quote for a stock.
    symbol: NSE trading symbol e.g. 'RELIANCE', 'SBIN', 'NIFTY 50'
    exchange: nse_cm (NSE equities), bse_cm (BSE equities), nse_fo (F&O)
    Returns: LTP, OHLC, volume, bid-ask, 52w high/low, circuit limits.
    """
    client = _get_client()
    result = _safe_call(
        client.search_scrip,
        exchange_segment=exchange,
        symbol=symbol,
    )
    if "error" in result:
        return result

    tokens = result if isinstance(result, list) else result.get("data", [])
    if not tokens:
        return {"error": f"Symbol {symbol} not found on {exchange}"}

    token = tokens[0].get("pSymbol") or tokens[0].get("instrumentToken")
    quote = _safe_call(
        client.quotes,
        instrument_tokens=[{"instrument_token": str(token), "exchange_segment": exchange}],
        quote_type="all",
    )
    return quote


@mcp.tool()
def check_margin(
    symbol: str,
    qty: int,
    price: float,
    order_type: str,
    product: str,
    transaction_type: str,
    exchange: str = "nse_cm",
) -> dict:
    """
    Check margin required before placing an order.
    order_type: L (limit), MKT (market), SL (stop-loss), SL-M
    product: MIS (intraday), CNC (delivery), NRML (F&O overnight)
    transaction_type: B (buy) or S (sell)
    Returns: required_margin, available_margin, is_sufficient.
    """
    client = _get_client()
    scrip = _safe_call(client.search_scrip, exchange_segment=exchange, symbol=symbol)
    tokens = scrip if isinstance(scrip, list) else scrip.get("data", [])
    if not tokens:
        return {"error": f"Symbol {symbol} not found"}

    token = str(tokens[0].get("pSymbol") or tokens[0].get("instrumentToken"))
    result = _safe_call(
        client.margin_required,
        exchange_segment=exchange,
        price=str(price),
        order_type=order_type,
        product=product,
        quantity=str(qty),
        instrument_token=token,
        transaction_type=transaction_type,
    )
    return result


# ─── EQUITY ORDER TOOLS ───────────────────────────────────────────────────────

@mcp.tool()
def place_order(
    symbol: str,
    action: str,
    qty: int,
    price: float,
    order_type: str,
    product: str,
    trigger_price: float = 0,
    tag: str = "CLAUDE_AUTO",
    exchange: str = "nse_cm",
    # Trading context — Claude Code passes these for Supabase trade recording
    tier: str = "EQUITY",
    stop_loss: float = 0,
    target: float = 0,
    composite_score: float | None = None,
    confidence: str | None = None,
    reasoning: str | None = None,
    close_order_id: str | None = None,
    realized_pnl: float | None = None,
) -> dict:
    """
    Place an equity order on NSE/BSE.
    action: BUY or SELL
    order_type: L (limit — use for most orders), MKT (market — avoid first 15 min), SL, SL-M
    product: MIS (intraday, square off by 3:15 PM), CNC (delivery, multi-day)
    price: set to 0 for MKT orders
    trigger_price: required for SL/SL-M orders
    close_order_id: pass the original order_id to close/square-off a trade (records exit in Supabase)
    Returns: order_id (nOrdNo) on success.
    """
    client = _get_client()

    trading_symbol = f"{symbol}-EQ" if exchange in ("nse_cm", "bse_cm") else symbol

    for attempt in range(3):
        result = _safe_call(
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
            log.info("Order placed: %s %s %d @ %.2f [%s]", action, symbol, qty, price, result)
            order_id = result.get("nOrdNo") or result.get("orderId") or result.get("order_id")
            if close_order_id:
                # Closing an existing position
                try:
                    sb_close_trade(close_order_id, price, realized_pnl or 0)
                except Exception as exc:
                    log.warning("Supabase close_trade failed: %s", exc)
            elif order_id and action.upper() == "BUY":
                # Opening a new long position
                try:
                    record_trade({
                        "order_id": str(order_id),
                        "symbol": symbol,
                        "tier": tier,
                        "action": action.upper(),
                        "product": product,
                        "qty": qty,
                        "entry_price": price or None,
                        "stop_loss": stop_loss or None,
                        "target": target or None,
                        "composite_score": composite_score,
                        "confidence": confidence,
                        "reasoning": reasoning,
                        "order_type": order_type,
                        "tag": tag,
                        "is_open": True,
                    })
                except Exception as exc:
                    log.warning("Supabase record_trade failed: %s", exc)
            return result
        if "429" in str(result.get("error", "")):
            time.sleep(2 ** attempt)
        else:
            break
    return result


@mcp.tool()
def place_stop_loss(
    symbol: str,
    action: str,
    qty: int,
    trigger_price: float,
    product: str,
    exchange: str = "nse_cm",
) -> dict:
    """
    Place a stop-loss order immediately after an entry order.
    action: SELL (for long positions) or BUY (for short positions)
    trigger_price: price at which SL activates
    Always place this IMMEDIATELY after place_order() succeeds.
    """
    return place_order(
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


@mcp.tool()
def cancel_order(order_id: str) -> dict:
    """Cancel a pending order by its order ID (nOrdNo from place_order response)."""
    return _safe_call(_get_client().cancel_order, order_id=order_id)


@mcp.tool()
def modify_order(
    order_id: str,
    price: float,
    qty: int,
    order_type: str,
    validity: str = "DAY",
    trigger_price: float = 0,
) -> dict:
    """
    Modify a pending order (e.g., trail stop-loss).
    order_type: L, MKT, SL, SL-M
    """
    return _safe_call(
        _get_client().modify_order,
        order_id=order_id,
        price=str(price),
        quantity=str(qty),
        order_type=order_type,
        validity=validity,
        trigger_price=str(trigger_price) if trigger_price else "0",
    )


# ─── F&O TOOLS ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_option_chain(symbol: str, expiry: str) -> dict:
    """
    Get full option chain for a stock/index.
    symbol: e.g. 'NIFTY', 'BANKNIFTY', 'RELIANCE'
    expiry: format DDMMMYYYY e.g. '29MAY2026'
    Returns: all strikes with CE/PE LTP, OI, volume, IV, bid-ask.
    Also computes PCR (Put-Call Ratio) and max pain strike.
    """
    client = _get_client()

    calls_result = _safe_call(
        client.search_scrip,
        exchange_segment="nse_fo",
        symbol=symbol,
        expiry=expiry,
        option_type="CE",
    )
    puts_result = _safe_call(
        client.search_scrip,
        exchange_segment="nse_fo",
        symbol=symbol,
        expiry=expiry,
        option_type="PE",
    )

    calls = calls_result if isinstance(calls_result, list) else calls_result.get("data", [])
    puts = puts_result if isinstance(puts_result, list) else puts_result.get("data", [])

    # Compute PCR and max pain
    total_call_oi = sum(float(c.get("openInterest", 0)) for c in calls)
    total_put_oi = sum(float(p.get("openInterest", 0)) for p in puts)
    pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 0

    # Max pain: strike where total options loss is maximum for option writers
    strikes = sorted(set(
        [float(c.get("strikePrice", 0)) for c in calls] +
        [float(p.get("strikePrice", 0)) for p in puts]
    ))
    max_pain_strike = None
    min_pain = float("inf")
    for s in strikes:
        call_pain = sum(max(0, s - float(c.get("strikePrice", 0))) * float(c.get("openInterest", 0)) for c in calls)
        put_pain = sum(max(0, float(p.get("strikePrice", 0)) - s) * float(p.get("openInterest", 0)) for p in puts)
        total = call_pain + put_pain
        if total < min_pain:
            min_pain = total
            max_pain_strike = s

    return {
        "symbol": symbol,
        "expiry": expiry,
        "pcr": pcr,
        "pcr_signal": "bullish" if pcr > 1.3 else "bearish" if pcr < 0.7 else "neutral",
        "max_pain_strike": max_pain_strike,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "calls": calls[:30],
        "puts": puts[:30],
    }


@mcp.tool()
def get_oi_data(symbol: str, expiry: str) -> dict:
    """
    Get Open Interest buildup/unwinding data across strikes.
    Use for: identifying support (high put OI), resistance (high call OI),
    and trend confirmation (rising OI in direction = conviction).
    """
    chain = get_option_chain(symbol, expiry)
    calls = chain.get("calls", [])
    puts = chain.get("puts", [])

    call_oi_by_strike = {
        c.get("strikePrice"): {
            "oi": float(c.get("openInterest", 0)),
            "oi_change": float(c.get("oiChange", 0)),
            "ltp": float(c.get("ltp", 0)),
        }
        for c in calls
    }
    put_oi_by_strike = {
        p.get("strikePrice"): {
            "oi": float(p.get("openInterest", 0)),
            "oi_change": float(p.get("oiChange", 0)),
            "ltp": float(p.get("ltp", 0)),
        }
        for p in puts
    }

    top_call_oi = sorted(call_oi_by_strike.items(), key=lambda x: x[1]["oi"], reverse=True)[:5]
    top_put_oi = sorted(put_oi_by_strike.items(), key=lambda x: x[1]["oi"], reverse=True)[:5]

    return {
        "symbol": symbol,
        "expiry": expiry,
        "pcr": chain["pcr"],
        "max_pain": chain["max_pain_strike"],
        "resistance_strikes": [{"strike": k, **v} for k, v in top_call_oi],
        "support_strikes": [{"strike": k, **v} for k, v in top_put_oi],
        "call_oi_by_strike": call_oi_by_strike,
        "put_oi_by_strike": put_oi_by_strike,
    }


@mcp.tool()
def place_fo_order(
    symbol: str,
    option_type: str,
    strike: float,
    expiry: str,
    action: str,
    qty: int,
    price: float,
    order_type: str = "L",
    # Trading context — Claude Code passes these for Supabase trade recording
    composite_score: float | None = None,
    confidence: str | None = None,
    reasoning: str | None = None,
    close_order_id: str | None = None,
    realized_pnl: float | None = None,
) -> dict:
    """
    Place an F&O options order.
    option_type: CE (Call) or PE (Put)
    strike: strike price e.g. 22500
    expiry: DDMMMYYYY format e.g. '29MAY2026'
    action: BUY (preferred — defined risk) or SELL (avoid — unlimited risk)
    qty: number of shares (lots × lot_size, e.g. NIFTY = 75 per lot)
    order_type: L (limit, preferred) or MKT
    close_order_id: pass the original order_id to close/square-off an F&O position
    ALWAYS buy options, never sell naked options (unlimited loss risk).
    """
    client = _get_client()

    scrip = _safe_call(
        client.search_scrip,
        exchange_segment="nse_fo",
        symbol=symbol,
        expiry=expiry,
        option_type=option_type,
        strike_price=str(int(strike)),
    )
    contracts = scrip if isinstance(scrip, list) else scrip.get("data", [])
    if not contracts:
        return {"error": f"No F&O contract found: {symbol} {strike} {option_type} {expiry}"}

    trading_symbol = contracts[0].get("pTrdSymbol") or contracts[0].get("tradingSymbol")

    for attempt in range(3):
        result = _safe_call(
            client.place_order,
            exchange_segment="nse_fo",
            product="NRML",
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
            trigger_price="0",
            tag="CLAUDE_FNO",
        )
        if "error" not in result:
            log.info("F&O order placed: %s %s %s %s @ %.2f", action, symbol, option_type, strike, price)
            order_id = result.get("nOrdNo") or result.get("orderId") or result.get("order_id")
            if close_order_id:
                try:
                    sb_close_trade(close_order_id, price, realized_pnl or 0)
                except Exception as exc:
                    log.warning("Supabase close_trade failed: %s", exc)
            elif order_id and action.upper() == "BUY":
                try:
                    record_trade({
                        "order_id": str(order_id),
                        "symbol": symbol,
                        "tier": "FNO",
                        "action": "BUY",
                        "product": "NRML",
                        "qty": qty,
                        "entry_price": price or None,
                        "option_type": option_type,
                        "strike": strike,
                        "expiry": expiry,
                        "premium_paid": price,
                        "composite_score": composite_score,
                        "confidence": confidence,
                        "reasoning": reasoning,
                        "order_type": order_type,
                        "tag": "CLAUDE_FNO",
                        "is_open": True,
                    })
                except Exception as exc:
                    log.warning("Supabase record_trade failed: %s", exc)
            return result
        if "429" in str(result.get("error", "")):
            time.sleep(2 ** attempt)
        else:
            break
    return result


@mcp.tool()
def get_fo_positions() -> list:
    """Get all open F&O positions with P&L, delta, and expiry."""
    positions = get_positions()
    return [p for p in positions if isinstance(p, dict) and p.get("exchangeSegment") in ("nse_fo", "bse_fo")]


# ─── UTILITY ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_portfolio_snapshot() -> dict:
    """
    Get a complete portfolio snapshot: positions, holdings, limits, and circuit breaker state.
    Use this at the start of every analysis session.
    """
    snapshot_path = Path(__file__).parent.parent / "data" / "portfolio" / "snapshot.json"
    file_snapshot = {}
    if snapshot_path.exists():
        try:
            file_snapshot = json.loads(snapshot_path.read_text())
        except Exception:
            pass

    limits = get_limits()
    positions = get_positions()
    fo_positions = get_fo_positions()

    return {
        "timestamp": datetime.now().isoformat(),
        "limits": limits,
        "equity_positions": positions,
        "fo_positions": fo_positions,
        "circuit_breaker": file_snapshot.get("circuit_breaker", {"tripped": False}),
        "daily_pnl": file_snapshot.get("daily_pnl", 0),
        "consecutive_losses": file_snapshot.get("consecutive_losses", 0),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
