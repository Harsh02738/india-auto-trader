"""
Kotak Neo MCP Server.

Exposes trading tools to Claude Code backed by:
  - Kotak Neo broker API (live) or PaperBroker (paper mode)
  - 10-strategy consensus engine (pure quant, no LLM)
  - Circuit breaker + Kelly-adjusted position sizing

Register in .mcp.json:
    {
      "mcpServers": {
        "kotak-neo": {
          "command": "C:\\Python314\\python.exe",
          "args": ["kotak_mcp_server.py"],
          "cwd": "<project root>"
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("kotak-neo", version="1.0.0")

MARKET_DIR  = Path("data/market")
JOURNAL_DIR = Path("data/journal")
SIGNAL_DIR  = Path("data/signals")


# ── Broker selection (paper vs live) ───────────────────────────────────────────

def _get_broker():
    from config.settings import settings
    if settings.paper_trading:
        from broker.paper_broker import PaperBroker
        return PaperBroker()
    from broker.kotak_direct import KotakBroker
    return KotakBroker()


# ── OHLCV loading (file → yfinance fallback) ────────────────────────────────────

def _load_ohlcv(symbol: str) -> dict:
    path = MARKET_DIR / f"{symbol}_ohlcv.json"
    if path.exists():
        return json.loads(path.read_text())
    return _yf_ohlcv(symbol)


def _load_fundamentals(symbol: str) -> dict | None:
    path = Path(f"data/fundamentals/{symbol}_fund.json")
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def _yf_ohlcv(symbol: str) -> dict:
    """Compute OHLCV snapshot from yfinance 5m data (15-min delayed)."""
    try:
        import yfinance as yf
        import pandas as pd
        from datetime import date as _date

        ticker = symbol if symbol.endswith(".NS") else symbol + ".NS"
        df = yf.download(ticker, interval="5m", period="5d", progress=False, auto_adjust=True)
        if df.empty:
            return {}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        bars = []
        for ts, row in df.iterrows():
            try:
                bars.append({
                    "t": ts.isoformat(),
                    "o": float(row["Open"]), "h": float(row["High"]),
                    "l": float(row["Low"]),  "c": float(row["Close"]),
                    "v": float(row["Volume"]),
                })
            except (TypeError, ValueError):
                continue

        if not bars:
            return {}

        def _ema(prev, new, n):
            k = 2.0 / (n + 1)
            return new * k + prev * (1 - k)

        seed = bars[0]["c"]
        ema9 = ema21 = ema50 = ema200 = ema12 = ema26 = seed
        prev_close = seed
        rsi_gains: list[float] = []
        rsi_losses: list[float] = []
        avg_gain = avg_loss = 0.0
        rsi_ready = False
        rsi = 50.0
        prev_bar = None
        atr_trs: list[float] = []
        atr_val = 0.0
        atr_ready = False
        vwap_num = vwap_den = 0.0
        bb_closes: list[float] = []
        vol_window: list[float] = []
        macd_hist = prev_macd = 0.0
        macd_crossover = False
        or_high = or_low = None
        today_str = _date.today().isoformat()

        for bar in bars:
            c, h, l, v = bar["c"], bar["h"], bar["l"], bar.get("v", 0.0)

            ema9   = _ema(ema9,   c, 9)
            ema21  = _ema(ema21,  c, 21)
            ema50  = _ema(ema50,  c, 50)
            ema200 = _ema(ema200, c, 200)
            ema12  = _ema(ema12,  c, 12)
            ema26  = _ema(ema26,  c, 26)
            prev_macd   = macd_hist
            macd_hist   = ema12 - ema26
            macd_crossover = bool(macd_hist > 0 and prev_macd <= 0)

            delta = c - prev_close
            gain, loss = max(delta, 0.0), max(-delta, 0.0)
            if not rsi_ready:
                rsi_gains.append(gain); rsi_losses.append(loss)
                if len(rsi_gains) >= 14:
                    avg_gain = sum(rsi_gains) / 14
                    avg_loss = sum(rsi_losses) / 14
                    rsi_ready = True
            else:
                avg_gain = (avg_gain * 13 + gain) / 14
                avg_loss = (avg_loss * 13 + loss) / 14
            if rsi_ready:
                rsi = round(100 - 100 / (1 + avg_gain / max(avg_loss, 1e-9)), 2)
            prev_close = c

            if prev_bar:
                tr = max(h - l, abs(h - prev_bar["c"]), abs(l - prev_bar["c"]))
                if not atr_ready:
                    atr_trs.append(tr)
                    if len(atr_trs) >= 14:
                        atr_val = sum(atr_trs) / 14
                        atr_ready = True
                else:
                    atr_val = (atr_val * 13 + tr) / 14
            prev_bar = bar

            tp = (h + l + c) / 3
            vwap_num += tp * v
            vwap_den += v

            bb_closes.append(c)
            if len(bb_closes) > 20:
                bb_closes.pop(0)
            vol_window.append(v)
            if len(vol_window) > 20:
                vol_window.pop(0)

            # Opening range (first two 5-min bars of today)
            if bar["t"].startswith(today_str):
                if or_high is None:
                    or_high = h; or_low = l
                else:
                    or_high = max(or_high, h); or_low = min(or_low, l)

        last = bars[-1]
        vwap = round(vwap_num / vwap_den, 2) if vwap_den else last["c"]
        bb_mean = sum(bb_closes) / len(bb_closes) if bb_closes else last["c"]
        import statistics as _st
        bb_std_val = _st.stdev(bb_closes) if len(bb_closes) > 1 else 0.0
        bb_upper = bb_mean + 2 * bb_std_val
        bb_lower = bb_mean - 2 * bb_std_val
        bb_pct = ((last["c"] - bb_lower) / max(bb_upper - bb_lower, 0.01))
        avg_vol = sum(vol_window) / len(vol_window) if vol_window else 1.0

        return {
            "symbol": symbol,
            "last_close": round(last["c"], 2),
            "open": round(last["o"], 2),
            "high": round(last["h"], 2),
            "low": round(last["l"], 2),
            "volume": int(last["v"]),
            "rsi": round(rsi, 2),
            "macd_hist": round(macd_hist, 4),
            "macd_crossover": macd_crossover,
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "ema50": round(ema50, 2),
            "ema200": round(ema200, 2),
            "above_ema200": last["c"] > ema200,
            "vwap": vwap,
            "above_vwap": last["c"] > vwap,
            "atr": round(atr_val, 2),
            "bb_upper": round(bb_upper, 2),
            "bb_lower": round(bb_lower, 2),
            "bb_pct": round(bb_pct, 4),
            "vol_ratio": round(last["v"] / max(avg_vol, 1.0), 2),
            "or_high": round(or_high, 2) if or_high else None,
            "or_low": round(or_low, 2) if or_low else None,
        }
    except Exception as exc:
        logger.warning("[yf_ohlcv] %s: %s", symbol, exc)
        return {}


# ── Journal helpers ─────────────────────────────────────────────────────────────

def _journal_path() -> Path:
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    return JOURNAL_DIR / "trades.jsonl"


def _read_journal(days: int = 90) -> list[dict]:
    path = _journal_path()
    if not path.exists():
        return []
    cutoff = datetime.now(tz=timezone.utc).timestamp() - days * 86400
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry.get("timestamp", "1970-01-01")).timestamp()
            if ts >= cutoff:
                entries.append(entry)
        except Exception:
            pass
    return entries


# ── MCP Tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def get_strategy_signals(symbol: str) -> dict:
    """
    Run all 10 quantitative strategies and return a consensus signal.

    Returns action (BUY/SELL/HOLD), vote_count, agreeing_strategies,
    combined_confidence, entry, stop_loss, target, risk_reward, reasoning.
    Requires vote_count >= 3 for a non-HOLD signal.
    """
    ohlcv = _load_ohlcv(symbol.upper())
    if not ohlcv:
        return {"error": f"No OHLCV data for {symbol}. Ensure data collector is running or yfinance is available."}

    fundamentals = _load_fundamentals(symbol.upper())

    from strategies.engine import StrategyEngine
    engine = StrategyEngine()
    sig = engine.evaluate(symbol.upper(), ohlcv, fundamentals)

    individual = {
        name: {
            "action": s.action,
            "confidence": round(s.confidence, 3),
            "reasoning": s.reasoning,
        }
        for name, s in sig.individual_signals.items()
    }

    return {
        "symbol": sig.symbol,
        "action": sig.action,
        "vote_count": sig.vote_count,
        "total_strategies": sig.total_strategies,
        "agreeing_strategies": sig.agreeing_strategies,
        "combined_confidence": sig.combined_confidence,
        "entry": sig.entry,
        "stop_loss": sig.stop_loss,
        "target": sig.target,
        "risk_reward": sig.risk_reward,
        "reasoning": sig.reasoning,
        "individual_votes": individual,
    }


@mcp.tool()
def get_portfolio_snapshot() -> dict:
    """
    Full portfolio state: positions, daily P&L, available cash, circuit breaker status.
    """
    from risk.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker()
    broker = _get_broker()

    equity = 0.0
    try:
        equity = broker.get_account_equity() if hasattr(broker, "get_account_equity") else 0.0
        cb.update_equity(equity)
    except Exception:
        pass

    positions = []
    try:
        positions = broker.get_positions()
    except Exception as exc:
        positions = [{"error": str(exc)}]

    cb_status = cb.status_report()

    return {
        "circuit_breaker": cb_status,
        "account_equity": equity,
        "open_positions": positions,
        "positions_count": len([p for p in positions if isinstance(p, dict) and not p.get("error")]),
    }


@mcp.tool()
def get_quote(symbol: str, exchange: str = "nse_cm") -> dict:
    """Real-time LTP, OHLC, and volume from Kotak Neo."""
    broker = _get_broker()
    if not hasattr(broker, "get_quote"):
        return {"error": "Live quotes not available in paper mode"}
    return broker.get_quote(symbol.upper(), exchange)


@mcp.tool()
def get_positions() -> list:
    """Open intraday (MIS) and overnight (CNC/NRML) positions."""
    return _get_broker().get_positions()


@mcp.tool()
def get_holdings() -> list:
    """Delivery holdings (T+2 settled)."""
    broker = _get_broker()
    if hasattr(broker, "get_holdings"):
        return broker.get_holdings()
    return []


@mcp.tool()
def get_order_book() -> list:
    """All orders placed today."""
    broker = _get_broker()
    if hasattr(broker, "get_order_book"):
        return broker.get_order_book()
    return []


@mcp.tool()
def get_limits(segment: str = "ALL", exchange: str = "ALL", product: str = "ALL") -> dict:
    """Available cash, margin, and utilization."""
    broker = _get_broker()
    if hasattr(broker, "get_limits"):
        return broker.get_limits(segment, exchange, product)
    return {"info": "Limits not available in paper mode"}


@mcp.tool()
def check_margin(
    symbol: str,
    qty: int,
    price: float,
    order_type: str = "L",
    product: str = "MIS",
    transaction_type: str = "BUY",
) -> dict:
    """
    Check required vs available margin for a potential order.
    Returns required_margin, available_margin, is_sufficient.
    """
    broker = _get_broker()
    limits = broker.get_limits() if hasattr(broker, "get_limits") else {}
    available = 0.0
    for key in ("Net", "net", "Available", "available", "availablecash"):
        val = limits.get(key) or (limits.get("data") or {}).get(key)
        if val:
            try:
                available = float(val)
                break
            except (TypeError, ValueError):
                pass

    required = qty * price * (0.20 if product == "MIS" else 1.0)
    return {
        "symbol": symbol.upper(),
        "qty": qty,
        "price": price,
        "required_margin": round(required, 2),
        "available_margin": round(available, 2),
        "is_sufficient": available >= required,
    }


@mcp.tool()
def place_order(
    symbol: str,
    action: str,
    qty: int,
    price: float,
    order_type: str = "L",
    product: str = "MIS",
    trigger_price: float = 0,
    tag: str = "CLAUDE",
) -> dict:
    """
    Place an equity order. Circuit breaker is checked first.

    action: BUY or SELL
    order_type: L (limit) | MKT | SL | SL-M
    product: MIS (intraday) | CNC (delivery)
    """
    from risk.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker()
    if cb.is_tripped():
        return {"error": "Circuit breaker TRIPPED — trading halted", "state": cb.state}

    broker = _get_broker()
    return broker.place_order(
        symbol=symbol.upper(),
        action=action.upper(),
        qty=qty,
        price=price,
        order_type=order_type,
        product=product,
        trigger_price=trigger_price,
        tag=tag,
    )


@mcp.tool()
def place_stop_loss(
    symbol: str,
    action: str,
    qty: int,
    trigger_price: float,
    product: str = "MIS",
) -> dict:
    """Place a SL-M stop-loss order immediately after entry."""
    broker = _get_broker()
    return broker.place_stop_loss(
        symbol=symbol.upper(),
        action=action.upper(),
        qty=qty,
        trigger_price=trigger_price,
        product=product,
    )


@mcp.tool()
def cancel_order(order_id: str) -> dict:
    """Cancel a pending order by order ID."""
    broker = _get_broker()
    if hasattr(broker, "cancel_order"):
        return broker.cancel_order(order_id)
    return {"error": "Cancel not supported in paper mode"}


@mcp.tool()
def modify_order(
    order_id: str,
    price: float,
    qty: int,
    order_type: str = "SL-M",
    validity: str = "DAY",
    trigger_price: float = 0,
) -> dict:
    """Modify a pending order — use to trail stop-loss."""
    broker = _get_broker()
    if hasattr(broker, "modify_order"):
        return broker.modify_order(
            order_id=order_id,
            price=price,
            qty=qty,
            order_type=order_type,
            validity=validity,
            trigger_price=trigger_price,
        )
    return {"error": "Modify not supported in paper mode"}


@mcp.tool()
def close_position(symbol: str, reason: str = "manual") -> dict:
    """Close an open position at market price."""
    broker = _get_broker()

    # Find open position quantity
    positions = broker.get_positions()
    qty = 0
    side = "SELL"
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        sym = (pos.get("trdSym") or pos.get("symbol") or pos.get("tradingSymbol") or "").replace("-EQ", "").upper()
        if sym == symbol.upper():
            qty = abs(int(pos.get("flBuyQty") or pos.get("netQty") or pos.get("quantity") or 0))
            net = int(pos.get("netQty") or pos.get("quantity") or 0)
            side = "SELL" if net > 0 else "BUY"
            break

    if qty == 0:
        return {"error": f"No open position found for {symbol}"}

    result = broker.place_order(
        symbol=symbol.upper(),
        action=side,
        qty=qty,
        price=0,
        order_type="MKT",
        product="MIS",
        tag=f"CLOSE_{reason[:10].upper()}",
    )
    logger.info("Closed %s qty=%d reason=%s → %s", symbol, qty, reason, result)
    return result


@mcp.tool()
def get_math_position_size(
    symbol: str,
    entry_price: float,
    stop_loss: float,
    strategy_name: str | None = None,
) -> dict:
    """
    Calculate Kelly-adjusted position size.

    Returns qty, risk_amount, notional, notional_pct, stop_loss_price, target_price, risk_reward.
    Applies half-Kelly cap from historical trade stats when >= 30 trades are available.
    """
    broker = _get_broker()
    equity = 500_000.0
    try:
        equity = broker.get_account_equity() if hasattr(broker, "get_account_equity") else equity
    except Exception:
        pass

    atr = abs(entry_price - stop_loss)
    if atr <= 0:
        return {"error": "entry_price and stop_loss must differ"}

    from risk.position_sizer import PositionSizer
    sizer = PositionSizer(account_equity=equity)
    result = sizer.equity(
        symbol=symbol.upper(),
        entry=entry_price,
        atr=atr,
        atr_stop_mult=1.0,
        strategy_name=strategy_name,
    )

    return {
        "symbol": symbol.upper(),
        "qty": result.qty,
        "risk_amount": result.risk_amount,
        "notional": result.notional,
        "notional_pct": result.notional_pct,
        "stop_loss_price": result.stop_loss_price,
        "target_price": result.target_price,
        "risk_reward": result.risk_reward,
        "kelly_applied": result.kelly_applied,
        "kelly_fraction": result.kelly_fraction,
        "account_equity": equity,
    }


# ── F&O Tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def get_option_chain(symbol: str, expiry: str) -> dict:
    """
    Fetch option chain with PCR and max pain.
    expiry format: 29MAY2026
    """
    broker = _get_broker()
    if hasattr(broker, "get_option_chain"):
        return broker.get_option_chain(symbol.upper(), expiry)

    # Fallback: read from cached file
    chain_path = Path(f"data/options/{symbol.upper()}_chain.json")
    if chain_path.exists():
        return json.loads(chain_path.read_text())
    return {"error": "Option chain not available. Ensure data collector is running."}


@mcp.tool()
def get_oi_data(symbol: str, expiry: str) -> dict:
    """OI buildup/unwinding, support and resistance strikes."""
    oi_path = Path(f"data/options/{symbol.upper()}_oi.json")
    if oi_path.exists():
        return json.loads(oi_path.read_text())
    return {"error": "OI data not available. Ensure data collector is running."}


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
) -> dict:
    """
    Place an F&O (options) order. Circuit breaker is checked first.

    option_type: CE or PE
    action: BUY or SELL (always BUY — never sell/write naked)
    expiry: 29MAY2026
    """
    from risk.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker()
    if cb.is_tripped():
        return {"error": "Circuit breaker TRIPPED — trading halted", "state": cb.state}

    if action.upper() == "SELL":
        return {"error": "Naked option writing is prohibited. Only BUY options (defined risk)."}

    broker = _get_broker()
    if hasattr(broker, "place_fo_order"):
        return broker.place_fo_order(symbol.upper(), option_type.upper(), strike, expiry, action.upper(), qty, price, order_type)

    # Fallback: map to place_order with F&O trading symbol
    fo_symbol = f"{symbol.upper()}{expiry}{int(strike)}{option_type.upper()}"
    return broker.place_order(
        symbol=fo_symbol,
        action=action.upper(),
        qty=qty,
        price=price,
        order_type=order_type,
        product="NRML",
        tag="FO_CLAUDE",
    )


@mcp.tool()
def get_fo_positions() -> list:
    """Open F&O (options/futures) positions."""
    broker = _get_broker()
    positions = broker.get_positions()
    fo = [p for p in positions if isinstance(p, dict) and
          any(x in str(p.get("trdSym") or p.get("symbol") or "") for x in ["CE", "PE", "FUT"])]
    return fo


# ── Trade Journal ───────────────────────────────────────────────────────────────


@mcp.tool()
def log_trade_outcome(
    symbol: str,
    final_pnl_pct: float,
    final_pnl_inr: float,
    outcome: str,
    strategy_votes: list[str],
    entry_price: float,
    exit_price: float,
    trade_order_id: str = "",
    lessons_text: str = "",
) -> dict:
    """
    Log a completed trade outcome to the trade journal.

    outcome: WIN | LOSS | BREAKEVEN
    strategy_votes: list of strategy names that agreed on the entry
    ALWAYS call this after every trade closes.
    """
    entry = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "date": str(date.today()),
        "symbol": symbol.upper(),
        "order_id": trade_order_id,
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "final_pnl_inr": round(final_pnl_inr, 2),
        "final_pnl_pct": round(final_pnl_pct, 4),
        "outcome": outcome.upper(),
        "strategy_votes": strategy_votes,
        "lessons": lessons_text,
    }

    path = _journal_path()
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")

    # Update circuit breaker with realized P&L
    from risk.circuit_breaker import CircuitBreaker
    CircuitBreaker().record_trade(final_pnl_inr)

    return {"logged": True, "entry": entry}


@mcp.tool()
def get_recent_trade_journal(limit: int = 10) -> list:
    """Return the last N completed trade journal entries."""
    entries = _read_journal(days=365)
    return entries[-limit:] if len(entries) >= limit else entries


@mcp.tool()
def get_strategy_performance_stats(strategy_name: str, days: int = 90) -> dict:
    """
    Win rate, EV, Kelly%, and risk-of-ruin for a specific strategy.
    Requires >= 30 trades for meaningful statistics.
    """
    entries = _read_journal(days=days)
    relevant = [e for e in entries if strategy_name in (e.get("strategy_votes") or [])]

    if not relevant:
        return {
            "strategy": strategy_name,
            "total_trades": 0,
            "note": f"No trades found for {strategy_name} in last {days} days",
        }

    wins  = [e for e in relevant if e.get("outcome") == "WIN"]
    losses = [e for e in relevant if e.get("outcome") == "LOSS"]
    total = len(relevant)
    win_rate = len(wins) / total if total else 0.0

    avg_win  = sum(e.get("final_pnl_pct", 0) for e in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(e.get("final_pnl_pct", 0) for e in losses) / len(losses)) if losses else 0.0

    ev = win_rate * avg_win - (1 - win_rate) * avg_loss
    kelly = (win_rate / avg_loss - (1 - win_rate) / avg_win) if avg_win > 0 and avg_loss > 0 else 0.0
    half_kelly = max(kelly / 2, 0.0)

    return {
        "strategy": strategy_name,
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "expected_value": round(ev, 4),
        "has_positive_edge": ev > 0,
        "kelly_fraction": round(kelly, 4),
        "half_kelly_fraction": round(half_kelly, 4),
        "days": days,
        "confidence_note": "Need >= 30 trades for statistical confidence" if total < 30 else "Sufficient data",
    }
