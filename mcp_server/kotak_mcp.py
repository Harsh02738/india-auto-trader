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


# ─── MEMORY ENGINEERING TOOLS ────────────────────────────────────────────────
# These tools implement the "memory loop" from the agentic trading framework:
# after each trade closes, outcomes are logged back to Supabase so the AI can
# learn from its own historical decisions and compute live EV/Kelly/RoR stats.

@mcp.tool()
def log_trade_outcome(
    trade_order_id: str,
    symbol: str,
    final_pnl_pct: float,
    final_pnl_inr: float,
    outcome: str,
    strategy_votes: str,
    entry_price: float,
    exit_price: float,
    stop_loss: float = 0,
    target: float = 0,
    vote_count: int = 0,
    market_context: str = "",
    lessons_text: str = "",
    tradingview_action: str = "HOLD",
    tradingview_score: float = 0.0,
    hold_duration_hours: float = 0.0,
    tier: str = "EQUITY",
) -> dict:
    """
    Log a trade outcome to the trade_journal table after a position closes.
    Called after every exit so the system builds a self-learning memory.

    outcome: WIN / LOSS / BREAKEVEN
    strategy_votes: comma-separated strategy names e.g. "MACD_RSI,VWAP,TradingView"
    final_pnl_pct: percentage P&L as decimal e.g. 0.023 for +2.3%
    market_context: brief note on market conditions at time of entry
    lessons_text: what worked / what didn't (used for self-review)
    """
    try:
        from supabase_client import db
        from datetime import datetime, timezone

        # Compute math stats at entry for reference
        ev_at_entry = kelly_at_entry = ror_at_entry = None
        ror_status = None
        try:
            from risk.math_engine import TradingMathEngine
            engine = TradingMathEngine()
            stats = engine.get_strategy_statistics(days=60)
            if stats.ev:
                ev_at_entry = stats.ev.expected_value
                kelly_at_entry = stats.ev.half_kelly_fraction
            if stats.ror:
                ror_at_entry = stats.ror.risk_of_ruin
                ror_status = stats.ror.status
        except Exception:
            pass

        tv_matched = tradingview_action == ("BUY" if final_pnl_pct > 0 else "SELL") and tradingview_action != "HOLD"

        payload = {
            "trade_order_id":       trade_order_id,
            "symbol":               symbol,
            "tier":                 tier,
            "strategy_votes":       strategy_votes,
            "vote_count":           vote_count,
            "tradingview_action":   tradingview_action,
            "tradingview_score":    tradingview_score if tradingview_score else None,
            "entry_price":          entry_price,
            "stop_loss":            stop_loss or None,
            "target":               target or None,
            "exit_price":           exit_price,
            "final_pnl_pct":        final_pnl_pct,
            "final_pnl_inr":        final_pnl_inr,
            "outcome":              outcome.upper(),
            "hold_duration_hours":  hold_duration_hours or None,
            "market_context":       market_context,
            "lessons_text":         lessons_text,
            "tv_matched_direction": tv_matched,
            "ev_at_entry":          ev_at_entry,
            "kelly_at_entry":       kelly_at_entry,
            "ror_at_entry":         ror_at_entry,
            "ror_status_at_entry":  ror_status,
            "closed_at":            datetime.now(tz=timezone.utc).isoformat(),
        }
        result = db.table("trade_journal").insert(payload).execute()
        log.info("Trade journal entry created: %s %s %s %.2f%%", symbol, outcome, strategy_votes, final_pnl_pct * 100)
        return {"status": "ok", "journal_id": result.data[0].get("id") if result.data else None}
    except Exception as exc:
        log.error("log_trade_outcome failed: %s", exc)
        return {"error": str(exc), "status": "failed"}


@mcp.tool()
def get_strategy_performance_stats(
    strategy_name: str = "",
    days: int = 90,
) -> dict:
    """
    Query trade_journal for win rate, avg win/loss, EV, Kelly fraction, Risk of Ruin.
    Use this before entering a trade to verify the strategy has a positive edge.

    strategy_name: filter by strategy e.g. "MACD_RSI" or "" for all strategies
    days: lookback period in days (default 90)

    Returns: win_rate, avg_win, avg_loss, EV, half_kelly%, risk_of_ruin, status, warnings.
    """
    try:
        from risk.math_engine import TradingMathEngine
        engine = TradingMathEngine()
        stats = engine.get_strategy_statistics(
            strategy_name=strategy_name or None,
            days=days,
        )
        verdict = engine.validate_strategy_edge(stats)

        result = {
            "strategy": strategy_name or "ALL",
            "days_analyzed": days,
            "total_trades": stats.total_trades,
            "wins": stats.wins,
            "losses": stats.losses,
            "win_rate": f"{stats.win_rate:.1%}",
            "data_source": stats.data_source,
            **verdict,
        }

        if stats.ev:
            result["expected_value_raw"] = stats.ev.expected_value
            result["half_kelly_raw"] = stats.ev.half_kelly_fraction
        if stats.ror:
            result["risk_of_ruin_raw"] = stats.ror.risk_of_ruin
            result["ror_status"] = stats.ror.status
            result["ror_message"] = stats.ror.message

        return result
    except Exception as exc:
        log.error("get_strategy_performance_stats failed: %s", exc)
        return {"error": str(exc), "status": "failed"}


@mcp.tool()
def get_recent_trade_journal(limit: int = 20) -> list:
    """
    Return the last N trade journal entries for self-review.
    Read this at the start of each session to learn from recent outcomes.
    Each entry includes entry conditions, strategy votes, TV signal, and P&L outcome.
    """
    try:
        from supabase_client import db
        result = (
            db.table("trade_journal")
            .select("id,symbol,tier,strategy_votes,vote_count,outcome,final_pnl_pct,"
                    "final_pnl_inr,entry_price,exit_price,tradingview_action,"
                    "tradingview_score,tv_matched_direction,market_context,"
                    "lessons_text,ev_at_entry,kelly_at_entry,ror_status_at_entry,"
                    "hold_duration_hours,created_at,closed_at")
            .order("closed_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = result.data or []
        # Format pnl_pct as % string for readability
        for r in rows:
            pnl = r.get("final_pnl_pct")
            if pnl is not None:
                r["pnl_display"] = f"{float(pnl)*100:+.2f}%"
        return rows
    except Exception as exc:
        log.error("get_recent_trade_journal failed: %s", exc)
        return [{"error": str(exc)}]


@mcp.tool()
def get_math_position_size(
    symbol: str,
    entry_price: float,
    stop_loss: float,
    account_equity: float = 0,
    strategy_name: str = "",
) -> dict:
    """
    Compute optimal position size using Kelly Criterion + ATR-based sizing.
    The result is the MINIMUM of: Kelly-based cap and ATR-based cap.
    This prevents over-betting even when ATR gives a large position.

    symbol: NSE symbol e.g. "RELIANCE"
    entry_price: planned entry price
    stop_loss: stop loss price
    account_equity: total account value (fetched from snapshot if 0)
    strategy_name: pass strategy name to pull Kelly from historical stats

    Returns: recommended_qty, max_risk_inr, kelly_fraction, kelly_applied, reasoning.
    """
    try:
        import math as _math
        from pathlib import Path
        import json

        # Get account equity
        if account_equity <= 0:
            snap_path = Path(__file__).parent.parent / "data" / "portfolio" / "snapshot.json"
            if snap_path.exists():
                snap = json.loads(snap_path.read_text())
                account_equity = snap.get("account_equity", 500_000)
            else:
                account_equity = 500_000

        stop_dist = abs(entry_price - stop_loss)
        if stop_dist <= 0:
            return {"error": "stop_loss must differ from entry_price"}

        # ATR-based size (2% account risk)
        risk_amount = account_equity * 0.02
        atr_qty = _math.floor(risk_amount / stop_dist)
        max_by_notional = _math.floor((account_equity * 0.05) / entry_price)
        atr_qty = max(1, min(atr_qty, max_by_notional))

        # Kelly-based size
        kelly_fraction = 0.0
        kelly_applied = False
        kelly_qty = atr_qty

        if strategy_name:
            try:
                from risk.math_engine import TradingMathEngine
                engine = TradingMathEngine()
                stats = engine.get_strategy_statistics(strategy_name, days=90)
                if stats.total_trades >= 30 and stats.ev and stats.ev.has_positive_edge:
                    kelly_fraction = stats.ev.half_kelly_fraction
                    kelly_max_notional = account_equity * kelly_fraction
                    kelly_qty = max(1, _math.floor(kelly_max_notional / entry_price))
                    if kelly_qty < atr_qty:
                        kelly_applied = True
            except Exception as ke:
                log.debug("Kelly calc skipped: %s", ke)

        final_qty = kelly_qty if kelly_applied else atr_qty
        notional = round(final_qty * entry_price, 2)
        risk_inr = round(final_qty * stop_dist, 2)

        return {
            "symbol": symbol,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "stop_distance": round(stop_dist, 2),
            "recommended_qty": final_qty,
            "notional_value": notional,
            "max_risk_inr": risk_inr,
            "risk_pct_of_equity": round(risk_inr / account_equity, 4),
            "kelly_fraction": round(kelly_fraction, 6),
            "kelly_applied": kelly_applied,
            "atr_qty_without_kelly": atr_qty,
            "account_equity_used": account_equity,
            "reasoning": (
                f"Kelly({kelly_fraction:.2%}) cap applied — reduced from {atr_qty} to {final_qty} shares"
                if kelly_applied else
                f"ATR-based sizing: {final_qty} shares (Kelly not applied — "
                + ("insufficient trade history" if not strategy_name else "Kelly not tighter than ATR")
                + ")"
            ),
        }

    except Exception as exc:
        log.error("get_math_position_size failed: %s", exc)
        return {"error": str(exc), "status": "failed"}


@mcp.tool()
def get_llm_analysis(symbol: str) -> dict:
    """
    Get a free LLM (Groq/Cerebras) trading signal for an NSE symbol.
    Reads available OHLCV, fundamentals, sentiment, and news data files,
    then queries the LLM for a BUY/SELL/HOLD verdict with confidence.
    This supplements your own indicators — it does NOT execute trades.

    symbol: NSE symbol e.g. "RELIANCE", "NIFTY", "SBIN"
    Returns: action (BUY/SELL/HOLD), confidence, reasoning.
    """
    try:
        import json
        from pathlib import Path
        from llm_analyzer.analyzer import LLMAnalyzer

        ohlcv: dict = {}
        ohlcv_path = Path(f"data/market/{symbol}_ohlcv.json")
        if ohlcv_path.exists():
            ohlcv = json.loads(ohlcv_path.read_text())

        fundamentals: dict | None = None
        fund_path = Path(f"data/fundamentals/{symbol}_fund.json")
        if fund_path.exists():
            fundamentals = json.loads(fund_path.read_text())

        sentiment: dict | None = None
        sent_path = Path(f"data/sentiment/{symbol}_sent.json")
        if sent_path.exists():
            sentiment = json.loads(sent_path.read_text())

        news: list | None = None
        news_path = Path(f"data/news/{symbol}_news.json")
        if news_path.exists():
            raw = json.loads(news_path.read_text())
            news = raw if isinstance(raw, list) else raw.get("items", [])

        if not ohlcv:
            return {"error": "No OHLCV data available", "symbol": symbol}

        analyzer = LLMAnalyzer()
        signal = analyzer.analyze(symbol, ohlcv, fundamentals, sentiment, news)

        if signal is None:
            return {"error": "LLM unavailable (check GROQ_API_KEY / CEREBRAS_API_KEY)", "symbol": symbol}

        result = {
            "symbol": symbol,
            "action": signal.action,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "target": signal.target,
            "risk_reward": signal.risk_reward,
        }
        log.info("[LLM] %s → %s (conf=%.2f)", symbol, signal.action, signal.confidence)
        return result
    except Exception as exc:
        log.error("get_llm_analysis failed: %s", exc)
        return {"error": str(exc), "symbol": symbol}


if __name__ == "__main__":
    mcp.run(transport="stdio")
