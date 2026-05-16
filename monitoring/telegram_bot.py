"""
Telegram trade confirmation bot.

Flow:
  1. Scanner detects new BUY/SELL signal → calls send_signal_alert(signal)
  2. Bot sends Telegram message with inline buttons [✅ Go  ❌ Skip]
  3. User taps "Go" → approved_trades set is updated
  4. Next scanner cycle picks up approved symbols → places Kotak Neo order

Run as a thread alongside intraday_scanner.py — do not run standalone.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from config.settings import settings

logger = logging.getLogger(__name__)

APPROVED_TRADES_FILE = Path("data/approved_trades.json")

# Thread-safe set of approved trade symbols
_approved: set[str] = set()
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_bot: Bot | None = None


# ── Public API (called from scanner thread) ────────────────────────────────────

def pop_approved() -> set[str]:
    """Return and clear all approved symbols. Called by scanner each cycle."""
    with _lock:
        approved = set(_approved)
        _approved.clear()
    return approved


def send_signal_alert(signal: dict) -> None:
    """Send a Telegram signal with Go/Skip buttons. Called from scanner thread."""
    if not _loop or not _bot:
        return
    asyncio.run_coroutine_threadsafe(_send_signal_coro(signal), _loop)


def send_text(text: str) -> None:
    """Send a plain text Telegram message. Called from scanner thread."""
    if not _loop or not _bot:
        return
    asyncio.run_coroutine_threadsafe(_send_text_coro(text), _loop)


# ── Async internals ────────────────────────────────────────────────────────────

async def _send_signal_coro(signal: dict) -> None:
    chat_id = getattr(settings, "telegram_chat_id", None)
    if not chat_id or not _bot:
        return

    sym    = signal["symbol"]
    action = signal["action"]
    score  = signal["composite_score"] * 100
    entry  = signal.get("entry_price")
    sl     = signal.get("stop_loss")
    tg     = signal.get("target")
    rr     = signal.get("risk_reward")
    conf   = signal.get("confidence", "")

    emoji = "📈" if action == "BUY" else "📉"
    lines = [
        f"{emoji} <b>NEW SIGNAL: {action} {sym}</b>",
        f"Score: <code>{score:.0f}/100</code>  [{conf}]",
    ]
    if entry: lines.append(f"Entry  ₹<code>{entry:.2f}</code>")
    if sl:    lines.append(f"SL     ₹<code>{sl:.2f}</code>")
    if tg:    lines.append(f"Target ₹<code>{tg:.2f}</code>")
    if rr:    lines.append(f"R:R    <code>{rr:.2f}</code>")
    lines.append(f"\n<i>{signal.get('reasoning', '')}</i>")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Go — Execute", callback_data=f"go|{sym}"),
        InlineKeyboardButton("❌ Skip",          callback_data=f"skip|{sym}"),
    ]])

    try:
        await _bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as exc:
        logger.debug("Telegram send error: %s", exc)


async def _send_text_coro(text: str) -> None:
    chat_id = getattr(settings, "telegram_chat_id", None)
    if not chat_id or not _bot:
        return
    try:
        await _bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as exc:
        logger.debug("Telegram send error: %s", exc)


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Go / Skip button presses."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if "|" not in data:
        return

    action, sym = data.split("|", 1)
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M IST")

    if action == "go":
        with _lock:
            _approved.add(sym)
        await query.edit_message_text(
            f"✅ <b>{sym} approved for execution</b>\nQueued at {ts} — will execute on next scan cycle.",
            parse_mode="HTML",
        )
        logger.info("Trade approved via Telegram: %s", sym)
    elif action == "skip":
        await query.edit_message_text(
            f"❌ <b>{sym} skipped</b>",
            parse_mode="HTML",
        )
        logger.info("Trade skipped via Telegram: %s", sym)


# ── Bot thread ─────────────────────────────────────────────────────────────────

async def _run_bot_async() -> None:
    global _bot

    token = getattr(settings, "telegram_bot_token", None)
    if not token:
        logger.warning("No TELEGRAM_BOT_TOKEN — Telegram bot disabled")
        return

    app = (
        Application.builder()
        .token(token)
        .build()
    )
    app.add_handler(CallbackQueryHandler(_handle_callback))
    _bot = app.bot

    logger.info("Telegram bot starting (polling)...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["callback_query"])

    # Keep running until the event loop is stopped
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

    token = getattr(settings, "telegram_bot_token", None)
    if not token:
        logger.info("Telegram bot skipped — no token configured")
        return False

    _loop = asyncio.new_event_loop()

    def _thread_target():
        asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(_run_bot_async())
        except Exception as exc:
            logger.error("Telegram bot error: %s", exc)

    thread = threading.Thread(target=_thread_target, daemon=True, name="telegram-bot")
    thread.start()
    logger.info("Telegram bot thread started")
    return True
