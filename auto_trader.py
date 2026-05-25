"""
Oracle Cloud Autonomous Trader.

Runs on Oracle Cloud Free Tier when the laptop is off.
Calls the Claude API (claude-sonnet-4-6) with the same MCP tools exposed as
function-call schemas, so the same intelligence runs remotely.

Cron schedule (Oracle Cloud, IST timezone):
    55 8  * * 1-5  python3 auto_trader.py --task pick-stocks
    15 9  * * 1-5  python3 auto_trader.py --task scan
    25 9  * * 1-5  python3 auto_trader.py --task scan
    */10 9-14 * * 1-5  python3 auto_trader.py --task scan
    10 15 * * 1-5  python3 auto_trader.py --task squareoff
    45 15 * * 1-5  python3 auto_trader.py --task eod

Or use deploy/auto_trader.cron for the full schedule.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Import MCP tool functions directly (same code, no network overhead on same VM)
from mcp_servers.claude_trader import (
    get_news_headlines,
    build_chart,
    get_strategy_signals,
    execute_trade,
    get_portfolio_status,
    get_open_positions,
    close_position,
    log_decision,
)

# Build tool schemas for Claude API function calling
TOOLS = [
    {
        "name": "get_news_headlines",
        "description": "Fetch top Indian financial news headlines from ET Markets, Moneycontrol, and NSE announcements.",
        "input_schema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": []},
    },
    {
        "name": "build_chart",
        "description": "Build a live candlestick chart with all indicators for the symbol. Returns base64 PNG + summary.",
        "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
    },
    {
        "name": "get_strategy_signals",
        "description": "Run all 10 quantitative strategies and return consensus vote breakdown.",
        "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
    },
    {
        "name": "get_ohlcv",
        "description": "Return latest OHLCV snapshot including RSI, MACD, VWAP, ATR, vol_ratio.",
        "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
    },
    {
        "name": "execute_trade",
        "description": "Execute a paper or live trade with position sizing and circuit breaker check.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":    {"type": "string"},
                "action":    {"type": "string", "enum": ["BUY", "SELL"]},
                "entry":     {"type": "number"},
                "stop_loss": {"type": "number"},
                "target":    {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["symbol", "action", "entry", "stop_loss", "target", "reasoning"],
        },
    },
    {
        "name": "get_portfolio_status",
        "description": "Return circuit breaker state, daily P&L, and equity.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_open_positions",
        "description": "Return all open positions with live P&L.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "close_position",
        "description": "Close an open position at market price.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "log_decision",
        "description": "Log Claude's trading decision to the daily decisions file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":         {"type": "string"},
                "action":         {"type": "string"},
                "confidence":     {"type": "number"},
                "reasoning":      {"type": "string"},
                "strategy_votes": {"type": "string"},
                "chart_path":     {"type": "string"},
                "executed":       {"type": "boolean"},
                "entry":          {"type": "number"},
                "stop_loss":      {"type": "number"},
                "target":         {"type": "number"},
            },
            "required": ["symbol", "action", "confidence", "reasoning"],
        },
    },
    {
        "name": "run_backtest",
        "description": "Run historical backtest on yfinance 5m data. Returns metrics + chart path.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "days": {"type": "integer"}},
            "required": ["symbol"],
        },
    },
]

_TOOL_MAP = {
    "get_news_headlines":  lambda a: get_news_headlines(**a),
    "build_chart":         lambda a: build_chart(**a),
    "get_strategy_signals": lambda a: get_strategy_signals(**a),
    "get_ohlcv":           lambda a: _load_ohlcv_wrapper(**a),
    "execute_trade":       lambda a: execute_trade(**a),
    "get_portfolio_status": lambda a: get_portfolio_status(),
    "get_open_positions":  lambda a: get_open_positions(),
    "close_position":      lambda a: close_position(**a),
    "log_decision":        lambda a: log_decision(**a),
    "run_backtest":        lambda a: _run_backtest_wrapper(**a),
}


def _load_ohlcv_wrapper(symbol: str) -> dict:
    from mcp_servers.claude_trader import _load_ohlcv
    return _load_ohlcv(symbol)


def _run_backtest_wrapper(symbol: str, days: int = 60) -> dict:
    from claude_trader.backtester import run_backtest
    return run_backtest(symbol, days=days).summary()


# ── Main agentic loop ──────────────────────────────────────────────────────────

def run_claude_task(task_prompt: str, max_turns: int = 20) -> str:
    """
    Send a task prompt to Claude (claude-sonnet-4-6) with MCP tools as function-call schemas.
    Runs the tool-use loop until Claude returns end_turn.
    """
    import anthropic
    from config.settings import settings

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    messages = [{"role": "user", "content": task_prompt}]

    for turn in range(max_turns):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            tools=TOOLS,
            messages=messages,
        )
        logger.info("[AutoTrader] Turn %d — stop_reason=%s", turn + 1, resp.stop_reason)

        if resp.stop_reason == "end_turn":
            # Extract final text
            for block in resp.content:
                if hasattr(block, "text"):
                    return block.text
            return "Done."

        if resp.stop_reason != "tool_use":
            break

        # Execute tool calls
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            tool_name = block.name
            tool_args = block.input or {}
            logger.info("[AutoTrader] Tool call: %s(%s)", tool_name, list(tool_args.keys()))
            try:
                fn = _TOOL_MAP.get(tool_name)
                if fn is None:
                    result = {"error": f"Unknown tool: {tool_name}"}
                else:
                    raw = fn(tool_args)
                    # Remove chart_base64 from result to avoid huge messages
                    if isinstance(raw, dict) and "chart_base64" in raw:
                        raw = {k: v for k, v in raw.items() if k != "chart_base64"}
                    result = raw
            except Exception as exc:
                logger.error("[AutoTrader] Tool %s error: %s", tool_name, exc)
                result = {"error": str(exc)}

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     json.dumps(result),
            })

        messages += [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": tool_results},
        ]

    return "Max turns reached."


# ── Task definitions ───────────────────────────────────────────────────────────

def task_pick_stocks() -> None:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    prompt = (
        f"Today is {today} (NSE trading day). "
        "Call get_news_headlines to fetch the latest Indian financial news. "
        "Analyze the headlines and identify exactly 8 NSE-listed stocks (Nifty 500) "
        "most likely to have significant intraday price movement today. "
        "Consider earnings, contracts, regulatory news, FII flows, and sector momentum. "
        "Save the picks to data/daily_stocks_{today}.json by calling log_decision once "
        "with action='PICK', symbol='UNIVERSE', reasoning=<your analysis>, "
        "strategy_votes=<comma-separated symbols>. "
        "Then send the picks via Telegram if possible."
    )
    result = run_claude_task(prompt)
    logger.info("[AutoTrader] pick-stocks result: %s", result[:200])


def task_scan() -> None:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    picks_path = Path(f"data/daily_stocks_{today}.json")
    if picks_path.exists():
        picks = json.loads(picks_path.read_text()).get("symbols", [])
    else:
        from daily_stock_picker import load_today_stocks
        picks = load_today_stocks()

    if not picks:
        logger.warning("[AutoTrader] No stocks to scan today")
        return

    symbols_str = ", ".join(picks)
    now_ist = datetime.now(IST).strftime("%H:%M")

    prompt = (
        f"It is {now_ist} IST on {today}. Run a market scan for today's NSE stocks: {symbols_str}.\n\n"
        "For each symbol:\n"
        "1. Call get_portfolio_status() first — if circuit breaker is tripped, stop immediately.\n"
        "2. Call get_open_positions() — skip any symbol already in a position.\n"
        "3. Call get_strategy_signals(symbol) — read the quantitative vote breakdown.\n"
        "4. Call build_chart(symbol) — analyze the chart carefully:\n"
        "   • Price vs VWAP and EMA-9/21\n"
        "   • Volume ratio vs average\n"
        "   • RSI level and direction\n"
        "   • Bollinger Band position\n"
        "   • OR breakout status (if or_high/or_low available)\n"
        "5. Make your BUY/SELL/HOLD decision combining chart analysis + strategy votes.\n"
        "6. Call log_decision(symbol, action, confidence, reasoning, ...) always.\n"
        "7. If action is BUY or SELL AND confidence >= 0.70:\n"
        "   Call execute_trade(symbol, action, entry, stop_loss, target, reasoning).\n\n"
        "Also check open positions for target/stop hits using get_open_positions() "
        "and close_position() if needed.\n\n"
        "Be decisive. The market waits for no one."
    )
    result = run_claude_task(prompt)
    logger.info("[AutoTrader] scan result: %s", result[:200])


def task_squareoff() -> None:
    prompt = (
        "It is 15:10 IST. MIS square-off time. "
        "Call get_open_positions() to list all open MIS positions. "
        "For each open MIS position, call close_position(symbol, reason='MIS square-off 15:10 IST'). "
        "After closing all, call get_portfolio_status() and report final daily P&L."
    )
    result = run_claude_task(prompt)
    logger.info("[AutoTrader] squareoff result: %s", result[:200])


def task_eod() -> None:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    prompt = (
        f"It is 15:45 IST, end of trading day {today}. "
        "Call get_portfolio_status() to get the final daily P&L and circuit breaker state. "
        "Call get_open_positions() to confirm all positions are closed. "
        "Summarise the day: total trades, P&L, win rate, any observations. "
        "Log a final entry via log_decision(symbol='EOD', action='REPORT', "
        "confidence=1.0, reasoning=<your summary>)."
    )
    result = run_claude_task(prompt)
    logger.info("[AutoTrader] EOD result: %s", result[:300])


# ── CLI ────────────────────────────────────────────────────────────────────────

TASKS = {
    "pick-stocks": task_pick_stocks,
    "scan":        task_scan,
    "squareoff":   task_squareoff,
    "eod":         task_eod,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Trader — autonomous runner")
    parser.add_argument("--task", choices=list(TASKS), required=True,
                        help="Task to run")
    args = parser.parse_args()

    logger.info("[AutoTrader] Starting task: %s", args.task)
    t0 = time.time()
    TASKS[args.task]()
    logger.info("[AutoTrader] Task '%s' completed in %.1fs", args.task, time.time() - t0)
