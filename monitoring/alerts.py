"""
Telegram alert system for trade events, circuit trips, and EOD reports.
"""

import logging
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode

from config.settings import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None


def _get_bot() -> Bot | None:
    global _bot
    if not settings.telegram_bot_token:
        return None
    if _bot is None:
        _bot = Bot(token=settings.telegram_bot_token)
    return _bot


async def _send(text: str) -> None:
    bot = _get_bot()
    if not bot or not settings.telegram_chat_id:
        logger.debug("Telegram not configured — suppressing alert")
        return
    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)


async def alert_circuit_tripped(reason: str) -> None:
    msg = (
        "🚨 <b>CIRCUIT BREAKER TRIPPED</b>\n\n"
        f"Reason: <code>{reason}</code>\n"
        f"Time: {datetime.now(tz=timezone.utc).strftime('%H:%M:%S UTC')}\n\n"
        "All new trade entries are <b>BLOCKED</b>.\n"
        "Manual reset required in Claude Code."
    )
    await _send(msg)


async def alert_circuit_warning(state: str, detail: str) -> None:
    msg = (
        f"⚠️ <b>CIRCUIT WARNING: {state}</b>\n"
        f"<code>{detail}</code>"
    )
    await _send(msg)


async def alert_trade_executed(
    symbol: str, action: str, qty: int, price: float,
    stop_loss: float, target: float, score: float, tier: str
) -> None:
    emoji = "🟢" if action == "BUY" else "🔴"
    msg = (
        f"{emoji} <b>{action} {symbol}</b> [{tier}]\n\n"
        f"Qty: <code>{qty}</code>  Price: <code>₹{price:.2f}</code>\n"
        f"SL:  <code>₹{stop_loss:.2f}</code>   Target: <code>₹{target:.2f}</code>\n"
        f"Score: <code>{score*100:.0f}/100</code>\n"
        f"Time: {datetime.now(tz=timezone.utc).strftime('%H:%M:%S UTC')}"
    )
    await _send(msg)


async def alert_stop_hit(symbol: str, pnl: float) -> None:
    emoji = "✅" if pnl >= 0 else "❌"
    msg = (
        f"{emoji} <b>Position Closed: {symbol}</b>\n"
        f"P&L: <code>₹{pnl:+.0f}</code>\n"
        f"Time: {datetime.now(tz=timezone.utc).strftime('%H:%M:%S UTC')}"
    )
    await _send(msg)


async def alert_new_signal(symbol: str, action: str, score: float, tier: str) -> None:
    emoji = "📈" if action == "BUY" else "📉"
    msg = (
        f"{emoji} New signal: <b>{action} {symbol}</b> [{tier}]\n"
        f"Confidence score: <code>{score*100:.0f}</code>"
    )
    await _send(msg)


async def send_eod_report(
    daily_pnl: float, realized_pnl: float, total_trades: int,
    win_rate: float, circuit_state: str
) -> None:
    emoji = "🟢" if daily_pnl >= 0 else "🔴"
    msg = (
        f"{emoji} <b>EOD Summary — {datetime.now().strftime('%d %b %Y')}</b>\n\n"
        f"Daily P&L:   <code>₹{daily_pnl:+,.0f}</code>\n"
        f"Realized:    <code>₹{realized_pnl:+,.0f}</code>\n"
        f"Trades:      <code>{total_trades}</code>\n"
        f"Win Rate:    <code>{win_rate:.1f}%</code>\n"
        f"Circuit:     <code>{circuit_state}</code>"
    )
    await _send(msg)


async def alert_penny_candidate(symbol: str, score: float, price: float) -> None:
    msg = (
        f"💰 <b>Penny Candidate: {symbol}</b>\n"
        f"Price: ₹{price:.2f}  Score: {score*100:.0f}\n"
        "⚠️ Max 1% portfolio. LIMIT order only."
    )
    await _send(msg)
