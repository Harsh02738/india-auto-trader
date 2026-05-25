"""
Claude Trader MCP Server.

Exposes trading tools to Claude Code so it can:
  - Fetch news headlines and pick stocks
  - Build and view live/backtest charts
  - Get strategy consensus signals
  - Execute paper or live trades
  - Monitor positions and portfolio

Run as a standalone MCP server:
    python -m mcp_servers.claude_trader

Register in Claude Code (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "claude-trader": {
          "command": "python",
          "args": ["-m", "mcp_servers.claude_trader"],
          "cwd": "<project root>"
        }
      }
    }
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as __main__
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("claude-trader", version="1.0.0")

MARKET_DIR  = Path("data/market")
REALTIME_DIR = Path("data/realtime")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_ohlcv(symbol: str) -> dict:
    path = MARKET_DIR / f"{symbol}_ohlcv.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _load_bars(symbol: str) -> list[dict]:
    path = REALTIME_DIR / f"{symbol}_1m.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, list) else data.get("bars", [])
        except Exception:
            pass
    return []


def _get_broker():
    from config.settings import settings
    if settings.paper_trading:
        from broker.paper_broker import PaperBroker
        return PaperBroker()
    from broker.kotak_direct import KotakBroker
    return KotakBroker()


# ── Tool: get_news_headlines ───────────────────────────────────────────────────

@mcp.tool()
def get_news_headlines(date: str = "") -> list[dict]:
    """
    Fetch top Indian financial news headlines from ET Markets, Moneycontrol RSS,
    and NSE corporate announcements. Claude Code reads these and decides which
    8 NSE stocks to trade today.

    Returns list of {source, headline, summary, url, symbol_hint}.
    """
    from claude_trader.news_picker import get_news_headlines as _fetch
    return _fetch(date or None)


# ── Tool: build_chart ─────────────────────────────────────────────────────────

@mcp.tool()
def build_chart(symbol: str) -> dict:
    """
    Build a live candlestick chart for the symbol with all indicators overlaid:
    EMA-9, EMA-21, VWAP, Bollinger Bands, OR high/low levels, RSI panel, volume panel.
    Annotates with the current strategy consensus signal (▲ BUY / ▼ SELL / — HOLD).

    Returns:
      chart_base64: PNG image as base64 string (Claude can view this directly)
      chart_path: local file path
      summary: {price, vwap, rsi, vol_ratio, or_high, or_low, action, votes}
    """
    ohlcv = _load_ohlcv(symbol)
    bars  = _load_bars(symbol)

    consensus = None
    try:
        from strategies.engine import StrategyEngine
        if ohlcv:
            consensus = StrategyEngine(min_votes=2).evaluate(symbol, ohlcv, use_llm=False)
    except Exception as exc:
        logger.warning("[MCP:build_chart] Strategy engine error for %s: %s", symbol, exc)

    from claude_trader.chart_builder import build_live_chart
    chart_path = build_live_chart(symbol, bars, ohlcv, consensus)

    b64 = base64.b64encode(chart_path.read_bytes()).decode()

    return {
        "chart_base64": b64,
        "chart_path": str(chart_path),
        "summary": {
            "price":     ohlcv.get("last_close"),
            "vwap":      ohlcv.get("vwap"),
            "rsi":       ohlcv.get("rsi"),
            "vol_ratio": ohlcv.get("vol_ratio"),
            "or_high":   ohlcv.get("or_high"),
            "or_low":    ohlcv.get("or_low"),
            "atr":       ohlcv.get("atr"),
            "action":    consensus.action if consensus else "HOLD",
            "votes":     f"{consensus.vote_count}/{consensus.total_strategies}" if consensus else "N/A",
            "confidence": consensus.combined_confidence if consensus else 0.0,
        },
    }


# ── Tool: get_strategy_signals ────────────────────────────────────────────────

@mcp.tool()
def get_strategy_signals(symbol: str) -> dict:
    """
    Run all 10 quantitative strategies on the latest OHLCV data for the symbol
    and return the full consensus signal breakdown.

    Returns consensus action, vote count, individual strategy votes,
    aggregated entry/SL/target, and reasoning string.
    """
    ohlcv = _load_ohlcv(symbol)
    if not ohlcv:
        return {"error": f"No OHLCV data for {symbol}. Is the realtime collector running?"}

    try:
        from strategies.engine import StrategyEngine
        sig = StrategyEngine(min_votes=2).evaluate(symbol, ohlcv, use_llm=False)
        individual = {
            name: {
                "action":     s.action,
                "confidence": round(s.confidence, 3),
                "stop_loss":  s.stop_loss,
                "target":     s.target,
            }
            for name, s in sig.individual_signals.items()
        }
        return {
            "symbol":             symbol,
            "action":             sig.action,
            "combined_confidence": round(sig.combined_confidence, 3),
            "vote_count":         sig.vote_count,
            "total_strategies":   sig.total_strategies,
            "agreeing_strategies": sig.agreeing_strategies,
            "entry":              sig.entry,
            "stop_loss":          sig.stop_loss,
            "target":             sig.target,
            "risk_reward":        sig.risk_reward,
            "reasoning":          sig.reasoning,
            "individual_signals": individual,
        }
    except Exception as exc:
        logger.error("[MCP:get_strategy_signals] %s: %s", symbol, exc)
        return {"error": str(exc)}


# ── Tool: get_ohlcv ───────────────────────────────────────────────────────────

@mcp.tool()
def get_ohlcv(symbol: str) -> dict:
    """
    Return the latest cached OHLCV snapshot for a symbol.
    Includes: last_close, rsi, macd_hist, vwap, atr, vol_ratio,
    or_high, or_low, prev_day_close, ema indicators, bb_pct.
    """
    ohlcv = _load_ohlcv(symbol)
    if not ohlcv:
        return {"error": f"No data for {symbol}"}
    return ohlcv


# ── Tool: execute_trade ───────────────────────────────────────────────────────

@mcp.tool()
def execute_trade(
    symbol: str,
    action: str,
    entry: float,
    stop_loss: float,
    target: float,
    reasoning: str,
) -> dict:
    """
    Execute a paper or live trade with ATR-based position sizing.

    Checks circuit breaker before placing. Uses PositionSizer for quantity
    (2% risk cap, 5% notional cap). Places main order then stop-loss immediately.
    Sends Telegram notification.

    action must be "BUY" or "SELL".
    Returns: {order, qty, risk_amount, paper, error?}
    """
    from risk.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker()
    if cb.is_tripped():
        return {"error": "Circuit breaker tripped — no new trades allowed today"}

    broker = _get_broker()
    equity = broker.get_account_equity()
    atr    = abs(entry - stop_loss) or entry * 0.02

    from risk.position_sizer import PositionSizer
    sizer  = PositionSizer(equity)
    sizing = sizer.equity(symbol, entry, atr)

    if sizing.qty <= 0:
        return {"error": f"Position size is 0 for {symbol} — check ATR/equity"}

    try:
        result = broker.place_order(
            symbol=symbol, action=action, qty=sizing.qty,
            price=entry, order_type="MKT", product="MIS", tag="CLAUDE_TRADER",
        )
        sl_action = "SELL" if action == "BUY" else "BUY"
        broker.place_stop_loss(symbol, sl_action, sizing.qty, stop_loss, "MIS")

        from config.settings import settings
        from claude_trader.daily_logger import log_trade
        log_trade(symbol, action, entry, stop_loss, target, sizing, reasoning)

        cb.record_trade(0)

        try:
            from monitoring.telegram_bot import send_text
            tag = "[PAPER] " if settings.paper_trading else ""
            rr  = round((target - entry) / max(abs(entry - stop_loss), 0.01), 2)
            send_text(
                f"{tag}{'🟢' if action=='BUY' else '🔴'} CLAUDE TRADER EXECUTED\n"
                f"{action} {symbol} × {sizing.qty} @ ₹{entry:.2f}\n"
                f"SL: ₹{stop_loss:.2f} | T: ₹{target:.2f} | R:R {rr:.1f}x\n"
                f"Reason: {reasoning[:120]}"
            )
        except Exception:
            pass

        return {
            "order":       result,
            "qty":         sizing.qty,
            "risk_amount": round(sizing.risk_amount, 2),
            "notional":    round(sizing.qty * entry, 2),
            "paper":       settings.paper_trading,
        }

    except Exception as exc:
        logger.error("[MCP:execute_trade] %s %s: %s", action, symbol, exc)
        return {"error": str(exc)}


# ── Tool: get_portfolio_status ────────────────────────────────────────────────

@mcp.tool()
def get_portfolio_status() -> dict:
    """
    Return full portfolio status: circuit breaker state, daily P&L,
    open positions count, and equity.
    """
    from risk.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker()
    status = cb.status_report()

    broker = _get_broker()
    try:
        equity = broker.get_account_equity()
        positions = broker.get_positions()
        status["equity"]          = equity
        status["open_positions"]  = len(positions)
        status["trading_allowed"] = cb.is_open()
    except Exception as exc:
        status["broker_error"] = str(exc)

    return status


# ── Tool: get_open_positions ──────────────────────────────────────────────────

@mcp.tool()
def get_open_positions() -> list[dict]:
    """
    Return all open positions with symbol, action, entry price, current LTP,
    unrealised P&L, stop-loss, and target.
    """
    broker = _get_broker()
    try:
        positions = broker.get_positions()
        result = []
        for pos in positions:
            sym = (pos.get("symbol") or pos.get("trdSym") or "").replace("-EQ", "").upper()
            ltp = broker.get_ltp(sym) or 0.0
            avg = float(pos.get("entry_price") or pos.get("avgPrice") or 0)
            qty = int(pos.get("qty") or pos.get("netQty") or 0)
            act = (pos.get("action") or "BUY").upper()
            pnl = (ltp - avg) * qty if act == "BUY" else (avg - ltp) * qty

            # Load signal for SL/Target
            sig_path = Path(f"data/signals/{sym}_signal.json")
            sig = json.loads(sig_path.read_text()) if sig_path.exists() else {}

            result.append({
                "symbol":      sym,
                "action":      act,
                "qty":         qty,
                "entry_price": avg,
                "ltp":         ltp,
                "unrealised_pnl": round(pnl, 2),
                "stop_loss":   sig.get("stop_loss"),
                "target":      sig.get("target"),
                "product":     pos.get("product") or pos.get("prd", "MIS"),
            })
        return result
    except Exception as exc:
        logger.error("[MCP:get_open_positions] %s", exc)
        return [{"error": str(exc)}]


# ── Tool: close_position ──────────────────────────────────────────────────────

@mcp.tool()
def close_position(symbol: str, reason: str = "") -> dict:
    """
    Close an open position for the given symbol at current market price.
    Records the trade outcome and sends a Telegram exit alert.
    """
    broker = _get_broker()
    try:
        ltp = broker.get_ltp(symbol)
        if ltp is None:
            return {"error": f"Could not get LTP for {symbol}"}

        from config.settings import settings
        if settings.paper_trading and hasattr(broker, "close_position"):
            result = broker.close_position(symbol, ltp)
        else:
            positions = broker.get_positions()
            pos = next((p for p in positions
                        if (p.get("symbol") or "").replace("-EQ", "").upper() == symbol), None)
            if not pos:
                return {"error": f"No open position for {symbol}"}
            qty    = abs(int(pos.get("qty") or pos.get("netQty") or 0))
            action = "SELL" if (pos.get("action") or "BUY").upper() == "BUY" else "BUY"
            result = broker.place_order(symbol, action, qty, 0, "MKT", "MIS", tag="CLAUDE_EXIT")

        try:
            from monitoring.telegram_bot import send_text
            send_text(f"🔲 EXIT: {symbol} @ ₹{ltp:.2f}\nReason: {reason or 'Manual'}")
        except Exception:
            pass

        return {"closed": symbol, "exit_price": ltp, "reason": reason, "result": result}

    except Exception as exc:
        logger.error("[MCP:close_position] %s: %s", symbol, exc)
        return {"error": str(exc)}


# ── Tool: log_decision ────────────────────────────────────────────────────────

@mcp.tool()
def log_decision(
    symbol: str,
    action: str,
    confidence: float,
    reasoning: str,
    strategy_votes: str = "",
    chart_path: str = "",
    executed: bool = False,
    entry: float = 0.0,
    stop_loss: float = 0.0,
    target: float = 0.0,
) -> dict:
    """
    Log Claude's trading decision to the daily decisions file.
    Call this after every /claude-scan analysis cycle, whether or not a trade was placed.
    """
    from claude_trader.daily_logger import log_analysis
    log_analysis(
        symbol=symbol,
        action=action,
        confidence=confidence,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        reasoning=reasoning,
        strategy_votes=strategy_votes,
        executed=executed,
        chart_path=chart_path,
    )
    return {"logged": True, "symbol": symbol, "action": action}


# ── Tool: run_backtest ────────────────────────────────────────────────────────

@mcp.tool()
def run_backtest(symbol: str, days: int = 60) -> dict:
    """
    Run a bar-by-bar backtest of all 10 strategies on historical 5m data
    (downloaded from yfinance).

    Returns metrics (win_rate, total_pnl, avg_win, avg_loss, max_drawdown, n_trades)
    and the path to a generated chart PNG showing signals + P&L curve.
    """
    from claude_trader.backtester import run_backtest as _run
    result = _run(symbol, days=days)
    return result.summary()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
