"""
Telegram Command Bot — notifications + command interface (paper trading mode).

Commands:
  /analyze SYMBOL  (or just type a symbol / company name)
  /positions       — open positions with P&L
  /exit SYMBOL     — immediately exit a position
  /status          — circuit breaker + daily P&L
  /pnl             — today's P&L summary
  /pause           — pause automated scanning
  /resume          — resume automated scanning
  /help            — list commands

Trades execute autonomously — this bot sends notifications only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config.settings import settings

logger = logging.getLogger(__name__)

PAUSE_FLAG = Path("data/portfolio/trading_paused.flag")
SNAPSHOT_FILE = Path("data/portfolio/snapshot.json")

# ── Shared state ───────────────────────────────────────────────────────────────
_loop: asyncio.AbstractEventLoop | None = None
_bot: Bot | None = None
_app: Application | None = None
_lock = threading.Lock()


def send_signal_alert(signal: dict) -> None:
    """Send a plain signal notification (no approval buttons — autonomous mode)."""
    if not _loop or not _bot:
        return
    asyncio.run_coroutine_threadsafe(_send_legacy_alert(signal), _loop)


def send_text(text: str) -> None:
    """Send a plain text Telegram message."""
    if not _loop or not _bot:
        return
    asyncio.run_coroutine_threadsafe(_send_text_coro(text), _loop)


# ── Async send helpers ─────────────────────────────────────────────────────────

async def _send_text_coro(text: str, parse_mode: str = "HTML") -> None:
    chat_id = settings.telegram_chat_id
    if not chat_id or not _bot:
        return
    try:
        await _bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except Exception as exc:
        logger.debug("Telegram send error: %s", exc)


async def _send_legacy_alert(signal: dict) -> None:
    chat_id = settings.telegram_chat_id
    if not chat_id or not _bot:
        return

    sym    = signal["symbol"]
    action = signal["action"]
    score  = signal.get("composite_score", 0) * 100
    entry  = signal.get("entry_price")
    sl     = signal.get("stop_loss")
    tg     = signal.get("target")
    rr     = signal.get("risk_reward")
    conf   = signal.get("confidence", "")

    emoji = "📈" if action == "BUY" else "📉"
    lines = [
        f"{emoji} <b>SIGNAL: {action} {sym}</b>",
        f"Score: <code>{score:.0f}/100</code>  [{conf}]",
    ]
    if entry: lines.append(f"Entry  ₹<code>{entry:.2f}</code>")
    if sl:    lines.append(f"SL     ₹<code>{sl:.2f}</code>")
    if tg:    lines.append(f"Target ₹<code>{tg:.2f}</code>")
    if rr:    lines.append(f"R:R    <code>{rr:.2f}</code>")
    lines.append(f"\n<i>{signal.get('reasoning', '')}</i>")

    try:
        await _bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.debug("Telegram send error: %s", exc)


# ── Analysis card sender (called by trade engine) ──────────────────────────────

def send_analysis_card(consensus) -> None:
    """Send a consensus analysis card with Execute/Skip buttons to Telegram."""
    if not _loop or not _bot:
        return
    asyncio.run_coroutine_threadsafe(_send_analysis_card_coro(consensus), _loop)


async def _send_analysis_card_coro(consensus) -> None:
    """Format and send the rich analysis card (info only — trades execute autonomously)."""
    chat_id = settings.telegram_chat_id
    if not chat_id or not _bot:
        return

    sym    = consensus.symbol
    action = consensus.action
    conf   = consensus.combined_confidence
    votes  = consensus.vote_count
    total  = consensus.total_strategies
    entry  = consensus.entry
    sl     = consensus.stop_loss
    tg     = consensus.target
    rr     = consensus.risk_reward

    emoji = "📈" if action == "BUY" else "📉"

    vote_lines = []
    for name, sig in consensus.individual_signals.items():
        icon = "✅" if sig.action == action else ("❌" if sig.action == "HOLD" else "🔻")
        vote_lines.append(f"  {icon} {name} ({sig.confidence:.2f})")

    tv_action = getattr(consensus, "tv_action", "HOLD")
    tv_score  = getattr(consensus, "tv_score", 0.0)
    tv_matched = getattr(consensus, "tv_matched", False)
    tv_line = ""
    if tv_action != "HOLD" and tv_score > 0:
        tv_icon = "✅" if tv_matched else "⚠️"
        tv_line = f"{tv_icon} TradingView: {tv_action} ({tv_score:.0%} confluence)"

    lines = [
        f"{emoji} <b>{sym} — Score: {conf:.0%} | {votes}/{total} strategies agree</b>",
        "🤖 <i>Auto-executing…</i>",
        "",
        "Strategy votes:",
    ] + vote_lines

    if tv_line:
        lines += ["", tv_line]

    lines += [
        "",
        f"Entry: ₹<code>{entry:.2f}</code>  |  SL: ₹<code>{sl:.2f}</code>  |  Target: ₹<code>{tg:.2f}</code>",
        f"R:R = <code>{rr:.1f}</code>",
        "",
        f"<i>{consensus.reasoning[:200]}</i>",
    ]

    tv_chart_url = f"https://www.tradingview.com/chart/?symbol=NSE:{sym}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 TV Chart", url=tv_chart_url)],
    ])

    try:
        await _bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as exc:
        logger.debug("Telegram card send error: %s", exc)


# ── Command Handlers ───────────────────────────────────────────────────────────

async def _cmd_help(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [
        "<b>India Auto-Trader Commands</b>",
        "",
        "/analyze SYMBOL — full strategy analysis + TV chart button",
        "  <i>or just type a symbol: RELIANCE</i>",
        "  <i>or company name: 'infosys'</i>",
        "/positions — open positions + P&L",
        "/exit SYMBOL — close a position now",
        "/status — circuit breaker + daily P&L",
        "/pnl — P&L summary",
        "/morning — EV stats, Risk of Ruin, circuit state",
        "/math [STRATEGY] — EV / Kelly / Risk of Ruin breakdown",
        "/journal — last 10 trade outcomes",
        "/pause — pause auto-scanning",
        "/resume — resume auto-scanning",
        "/help — this message",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = (context.args or [])
    symbol_input = " ".join(args).strip().upper()
    if not symbol_input:
        await update.message.reply_text("Usage: /analyze SYMBOL  (e.g. /analyze RELIANCE)")
        return
    await _run_analysis(update, symbol_input)


async def _cmd_positions(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Fetching positions…")
    try:
        if settings.paper_trading:
            from broker.paper_broker import PaperBroker
            broker = PaperBroker()
        else:
            from broker.kotak_direct import KotakBroker
            broker = KotakBroker()
        positions = broker.get_positions()
        if not positions:
            await update.message.reply_text("No open positions.")
            return

        label = "[PAPER] " if settings.paper_trading else ""
        lines = [f"<b>{label}Open Positions</b>", ""]
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            sym = (pos.get("trdSym") or pos.get("symbol") or "?").replace("-EQ", "")
            qty = pos.get("flBuyQty") or pos.get("netQty") or pos.get("quantity") or 0
            avg = pos.get("avgPrice") or pos.get("average_price") or 0
            ltp = pos.get("ltp") or pos.get("lastPrice") or 0
            pnl = (float(ltp) - float(avg)) * int(qty) if avg and ltp else 0
            sign = "+" if pnl >= 0 else ""
            lines.append(f"<b>{sym}</b> qty={qty} avg=₹{avg} ltp=₹{ltp} P&L: {sign}₹{pnl:.0f}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        logger.error("positions error: %s", exc)
        await update.message.reply_text(f"Error fetching positions: {exc}")


async def _cmd_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = (context.args or [])
    if not args:
        await update.message.reply_text("Usage: /exit SYMBOL")
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"⏳ Closing {symbol} at market…")
    try:
        if settings.paper_trading:
            from broker.paper_broker import PaperBroker
            broker = PaperBroker()
            ltp = broker.get_ltp(symbol)
            if ltp is None:
                await update.message.reply_text(f"No live price available for {symbol}.")
                return
            success = broker.close_position(symbol, ltp)
            if success:
                await update.message.reply_text(f"✅ [PAPER] Closed {symbol} at ₹{ltp:.2f}")
            else:
                await update.message.reply_text(f"❌ No open paper position found for {symbol}.")
        else:
            from broker.kotak_direct import KotakBroker
            broker = KotakBroker()
            pos = broker.get_open_position(symbol)
            if not pos:
                await update.message.reply_text(f"No open position found for {symbol}.")
                return
            qty = int(pos.get("flBuyQty") or pos.get("netQty") or pos.get("quantity") or 0)
            product = pos.get("product") or pos.get("prd") or "MIS"
            result = broker.place_order(symbol, "SELL", abs(qty), 0, "MKT", product, tag="MANUAL_EXIT")
            order_id = broker.extract_order_id(result)
            if order_id:
                await update.message.reply_text(f"✅ Exit order placed for {symbol} qty={abs(qty)}. Order ID: {order_id}")
            else:
                await update.message.reply_text(f"❌ Exit failed: {result.get('error', result)}")
    except Exception as exc:
        logger.error("exit error: %s", exc)
        await update.message.reply_text(f"Error: {exc}")


async def _cmd_status(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        snap = {}
        if SNAPSHOT_FILE.exists():
            snap = json.loads(SNAPSHOT_FILE.read_text())

        cb = snap.get("circuit_breaker", {})
        tripped = cb.get("tripped", False)
        cb_state = "🚨 TRIPPED" if tripped else "✅ SAFE"
        reason = cb.get("reason", "")
        daily_pnl = snap.get("daily_pnl", 0)
        consec = snap.get("consecutive_losses", 0)
        paused = PAUSE_FLAG.exists()

        sign = "+" if daily_pnl >= 0 else ""
        lines = [
            "<b>Trading Status</b>",
            f"Circuit Breaker: {cb_state}",
        ]
        if reason:
            lines.append(f"  Reason: {reason}")
        lines += [
            f"Daily P&L: {sign}₹{daily_pnl:,.0f}",
            f"Consecutive losses: {consec}",
            f"Auto-scan: {'⏸ PAUSED' if paused else '▶ ACTIVE'}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Error reading status: {exc}")


async def _cmd_pnl(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        eod_path = Path(f"data/eod/{today}.json")
        if eod_path.exists():
            data = json.loads(eod_path.read_text())
            pnl  = data.get("realized_pnl", 0)
            trades = data.get("total_trades", 0)
            wr   = data.get("win_rate", 0) * 100
            sign = "+" if pnl >= 0 else ""
            await update.message.reply_text(
                f"<b>P&L — {today}</b>\nRealized: {sign}₹{pnl:,.0f}\n"
                f"Trades: {trades}  Win rate: {wr:.0f}%",
                parse_mode="HTML",
            )
        else:
            snap = {}
            if SNAPSHOT_FILE.exists():
                snap = json.loads(SNAPSHOT_FILE.read_text())
            pnl = snap.get("daily_pnl", 0)
            sign = "+" if pnl >= 0 else ""
            await update.message.reply_text(
                f"<b>Running P&L — {today}</b>\n{sign}₹{pnl:,.0f} (intraday unrealized)",
                parse_mode="HTML",
            )
    except Exception as exc:
        await update.message.reply_text(f"P&L error: {exc}")


async def _cmd_morning(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Morning update: EV stats, Risk of Ruin, circuit state, TV watchlist top movers."""
    await update.message.reply_text("⏳ Generating morning brief…")
    try:
        from risk.math_engine import TradingMathEngine
        engine = TradingMathEngine()
        stats = engine.get_strategy_statistics(days=90)

        # Circuit state
        snap = {}
        if SNAPSHOT_FILE.exists():
            snap = json.loads(SNAPSHOT_FILE.read_text())
        cb = snap.get("circuit_breaker", {})
        cb_state = "🚨 TRIPPED" if cb.get("tripped") else "✅ SAFE"
        daily_pnl = snap.get("daily_pnl", 0)
        consec = snap.get("consecutive_losses", 0)

        lines = [
            "<b>Morning Brief</b>",
            "",
            f"Circuit: {cb_state}  |  Daily P&L: ₹{daily_pnl:+,.0f}  |  Consec losses: {consec}",
            "",
            "<b>Strategy Math (last 90 days)</b>",
        ]

        if stats.total_trades == 0:
            lines.append("No closed trades on record yet.")
        else:
            ev = stats.ev
            ror = stats.ror
            win_str = f"{stats.win_rate:.1%}" if stats.win_rate else "N/A"
            ev_str = f"{ev.expected_value:+.4f}" if ev else "N/A"
            kelly_str = f"{ev.half_kelly_fraction:.2%}" if ev else "N/A"
            ror_str = f"{ror.risk_of_ruin:.2%}" if ror else "N/A"
            ror_emoji = {"SAFE": "✅", "CAUTION": "⚠️", "DANGER": "🔴", "HALT": "🚨"}.get(
                ror.status if ror else "", "❓"
            )
            lines += [
                f"Trades: {stats.total_trades}  |  Win rate: {win_str}",
                f"EV/trade: <code>{ev_str}</code>  |  Half-Kelly: <code>{kelly_str}</code>",
                f"Risk of Ruin: <code>{ror_str}</code>  {ror_emoji}",
            ]
            if ev and ev.confidence_level != "SUFFICIENT":
                lines.append(f"⚠️ {ev.confidence_level}: {stats.total_trades} trades (need 300+)")
            if ror and ror.status in ("DANGER", "HALT"):
                lines.append(f"🚨 {ror.message}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as exc:
        logger.error("morning error: %s", exc)
        await update.message.reply_text(f"Morning brief error: {exc}")


async def _cmd_math(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show EV/Kelly/RoR math for a strategy or overall portfolio."""
    args = context.args or []
    strategy = " ".join(args).strip() if args else ""
    await update.message.reply_text(f"⏳ Computing math stats{' for ' + strategy if strategy else ''}…")
    try:
        from risk.math_engine import TradingMathEngine
        engine = TradingMathEngine()
        stats = engine.get_strategy_statistics(strategy_name=strategy or None, days=90)
        verdict = engine.validate_strategy_edge(stats)

        lines = [
            f"<b>Trade Math — {strategy or 'ALL strategies'} (90d)</b>",
            "",
            f"Trades: {stats.total_trades}  |  Wins: {stats.wins}  |  Losses: {stats.losses}",
        ]

        if stats.ev:
            ev = stats.ev
            lines += [
                f"Win rate: <code>{ev.win_rate:.1%}</code>",
                f"Avg win:  <code>{ev.avg_win_pct:.2%}</code>  |  Avg loss: <code>{ev.avg_loss_pct:.2%}</code>",
                f"EV/trade: <code>{ev.expected_value:+.4f}</code>  ({'POSITIVE' if ev.has_positive_edge else 'NEGATIVE'})",
                f"Half-Kelly size: <code>{ev.half_kelly_fraction:.2%}</code>",
                f"Break-even required after loss: <code>{ev.break_even_required:.2%}</code>",
                f"Confidence: {ev.confidence_level} ({ev.sample_size} trades)",
            ]
            for w in ev.warnings:
                lines.append(f"⚠️ {w}")

        if stats.ror:
            ror = stats.ror
            ror_emoji = {"SAFE": "✅", "CAUTION": "⚠️", "DANGER": "🔴", "HALT": "🚨"}.get(ror.status, "❓")
            lines += [
                "",
                f"Risk of Ruin: <code>{ror.risk_of_ruin:.2%}</code>  {ror_emoji}  [{ror.status}]",
                f"Recommended size: <code>{ror.recommended_position_pct:.2%}</code> of capital",
            ]

        lines += ["", f"Verdict: <b>{verdict.get('verdict', 'N/A')}</b>"]

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Math error: {exc}")


async def _cmd_journal(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last 10 trade journal entries."""
    await update.message.reply_text("⏳ Fetching trade journal…")
    try:
        from local_db import get_journal_entries
        rows = get_journal_entries(days=90)[:10]
        if not rows:
            await update.message.reply_text("No journal entries yet. Log outcomes after each trade with log_trade_outcome().")
            return

        lines = ["<b>Recent Trade Journal (last 10)</b>", ""]
        for r in rows:
            outcome = r.get("outcome", "?")
            pnl = float(r.get("final_pnl_pct", 0)) * 100
            sym = r.get("symbol", "?")
            tv_ok = "📺✅" if r.get("tv_matched_direction") else "📺❌"
            strats = (r.get("strategy_votes", "") or "")[:30]
            emoji = "✅" if outcome == "WIN" else ("❌" if outcome == "LOSS" else "➖")
            lines.append(f"{emoji} <b>{sym}</b> {outcome}  {pnl:+.1f}%  {tv_ok}  <i>{strats}</i>")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"Journal error: {exc}")


async def _cmd_pause(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_FLAG.write_text(datetime.now(tz=timezone.utc).isoformat())
    await update.message.reply_text("⏸ Automated scanning paused. Send /resume to restart.")


async def _cmd_resume(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if PAUSE_FLAG.exists():
        PAUSE_FLAG.unlink()
        await update.message.reply_text("▶ Automated scanning resumed.")
    else:
        await update.message.reply_text("Scanning was not paused.")


# ── Free-text message handler ──────────────────────────────────────────────────

async def _handle_message(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parse free text to extract a stock symbol and run analysis."""
    text = (update.message.text or "").strip()
    symbol = _resolve_symbol(text)
    if symbol:
        await _run_analysis(update, symbol)
    else:
        await update.message.reply_text(
            f"Could not identify a stock symbol in '{text}'.\n"
            "Try /analyze RELIANCE or just type: RELIANCE"
        )


# ── Analysis runner ────────────────────────────────────────────────────────────

async def _run_analysis(update: Update, symbol: str) -> None:
    """Fetch data, run strategy engine, send analysis card."""
    await update.message.reply_text(f"⏳ Analysing <b>{symbol}</b>…", parse_mode="HTML")
    try:
        ohlcv, fundamentals = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_data, symbol
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ Data fetch failed: {exc}")
        return

    if not ohlcv:
        await update.message.reply_text(f"❌ No OHLCV data for {symbol}. Check the symbol.")
        return

    try:
        from strategies.engine import StrategyEngine
        engine = StrategyEngine()
        consensus = engine.evaluate(symbol, ohlcv, fundamentals)
    except Exception as exc:
        logger.error("Strategy engine error for %s: %s", symbol, exc)
        await update.message.reply_text(f"❌ Analysis error: {exc}")
        return

    await _send_analysis_card_coro(consensus)


def _fetch_data(symbol: str) -> tuple[dict, dict | None]:
    """Blocking: fetch OHLCV and fundamentals (called in executor)."""
    from data_collector.market_data import collect_daily
    from pathlib import Path
    import json

    ohlcv = {}
    # Try cached file first to avoid rate limits
    cache_path = Path(f"data/market/{symbol}_ohlcv.json")
    if cache_path.exists():
        try:
            ohlcv = json.loads(cache_path.read_text())
        except Exception:
            pass

    # Refresh from yfinance if stale or missing
    if not ohlcv:
        ohlcv = collect_daily(symbol)

    fundamentals = None
    fund_path = Path(f"data/fundamentals/{symbol}_fund.json")
    if fund_path.exists():
        try:
            fundamentals = json.loads(fund_path.read_text())
        except Exception:
            pass

    return ohlcv, fundamentals


# ── Symbol resolution ─────────────────────────────────────────────────────────

def _resolve_symbol(text: str) -> str | None:
    """
    Try to map free-text input to an NSE symbol.
    Handles: "RELIANCE", "reliance", "analyze Infosys", "hdfc bank", etc.
    """
    from config.instruments import NIFTY_200

    text_clean = text.strip().upper()

    # Strip common prefixes
    for prefix in ("ANALYZE ", "BUY ", "SELL ", "CHECK "):
        if text_clean.startswith(prefix):
            text_clean = text_clean[len(prefix):].strip()

    # Direct symbol match (e.g., "RELIANCE", "TCS")
    symbol_pattern = re.compile(r"^[A-Z&]{2,15}$")
    if symbol_pattern.match(text_clean):
        # Verify it's in our universe
        symbols = {inst.symbol for inst in NIFTY_200}
        if text_clean in symbols:
            return text_clean
        # Could still be valid even if not in Nifty200 — return as-is
        if len(text_clean) >= 2:
            return text_clean

    # Fuzzy name match against NIFTY_200 company names
    text_lower = text.lower()
    best_sym = None
    best_score = 0
    for inst in NIFTY_200:
        name_lower = inst.name.lower()
        sym_lower  = inst.symbol.lower()
        # Check if text is a substring of the name or vice versa
        if text_lower in name_lower or sym_lower.startswith(text_lower):
            score = len(text_lower)
            if score > best_score:
                best_score = score
                best_sym = inst.symbol
        elif name_lower.startswith(text_lower):
            score = len(text_lower) + 5
            if score > best_score:
                best_score = score
                best_sym = inst.symbol

    return best_sym


# ── Bot startup ────────────────────────────────────────────────────────────────

async def _run_bot_async() -> None:
    global _bot, _app

    token = settings.telegram_bot_token
    if not token:
        logger.warning("No TELEGRAM_BOT_TOKEN — Telegram bot disabled")
        return

    app = Application.builder().token(token).build()

    # Commands
    app.add_handler(CommandHandler("help",      _cmd_help))
    app.add_handler(CommandHandler("start",     _cmd_help))
    app.add_handler(CommandHandler("analyze",   _cmd_analyze))
    app.add_handler(CommandHandler("positions", _cmd_positions))
    app.add_handler(CommandHandler("exit",      _cmd_exit))
    app.add_handler(CommandHandler("status",    _cmd_status))
    app.add_handler(CommandHandler("pnl",       _cmd_pnl))
    app.add_handler(CommandHandler("pause",     _cmd_pause))
    app.add_handler(CommandHandler("resume",    _cmd_resume))
    app.add_handler(CommandHandler("morning",   _cmd_morning))
    app.add_handler(CommandHandler("math",      _cmd_math))
    app.add_handler(CommandHandler("journal",   _cmd_journal))

    # Free text (non-command messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    _bot = app.bot
    _app = app

    logger.info("Telegram command bot starting…")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message"])

    try:
        await asyncio.get_event_loop().create_future()
    except asyncio.CancelledError:
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def start_bot_thread() -> bool:
    """Start the Telegram bot in a daemon thread. Returns True if started."""
    global _loop

    token = settings.telegram_bot_token
    if not token:
        logger.info("Telegram bot skipped — no token configured")
        return False

    _loop = asyncio.new_event_loop()

    def _target():
        asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(_run_bot_async())
        except Exception as exc:
            logger.error("Telegram bot thread error: %s", exc)

    thread = threading.Thread(target=_target, daemon=True, name="telegram-bot")
    thread.start()
    logger.info("Telegram bot thread started")
    return True
