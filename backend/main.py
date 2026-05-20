"""
FastAPI backend for India Auto-Trader.
Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from backend.db.models import init_db
from backend.routes import portfolio, signals, options, earnings, penny, trades, pnl, ws
from backend.routes.ws import _push_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("backend.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    logger.info("Database initialised")
    ws_task = asyncio.create_task(_push_loop())
    logger.info("WebSocket push loop started")
    yield
    # Shutdown
    ws_task.cancel()


app = FastAPI(
    title="India Auto-Trader API",
    version="0.1.0",
    description="AI-powered NSE/BSE automated trading backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(portfolio.router)
app.include_router(signals.router)
app.include_router(options.router)
app.include_router(earnings.router)
app.include_router(penny.router)
app.include_router(trades.router)
app.include_router(pnl.router)
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── TradingView Pine Script Webhook ───────────────────────────────────────────
# Configure a webhook alert in TradingView pointing to:
#   POST http://<your-server>:8000/webhook/tradingview
# Set the TradingView alert message body to JSON:
#   {"symbol":"RELIANCE","action":"BUY","price":{{close}},"strategy":"MyPineScript"}
# Set header X-TV-Secret to the value of TRADINGVIEW_WEBHOOK_SECRET in .env

_TV_SECRET = os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", "")


@app.post("/webhook/tradingview")
async def tradingview_webhook(
    request: Request,
    x_tv_secret: str = Header(default=""),
) -> dict:
    """
    Receives Pine Script strategy alerts from TradingView.
    Validates the secret token, runs the consensus engine on the signal,
    and sends a Telegram HITL approval card if consensus passes.

    Pine Script alert message format (set in TradingView alert dialog):
    {
      "symbol": "RELIANCE",
      "action": "BUY",
      "price": {{close}},
      "strategy": "MyPineScript",
      "timeframe": "{{interval}}"
    }
    """
    # Validate secret
    if _TV_SECRET and x_tv_secret != _TV_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    symbol   = str(body.get("symbol", "")).upper().strip()
    action   = str(body.get("action", "")).upper().strip()
    price    = float(body.get("price", 0))
    strategy = str(body.get("strategy", "TradingView"))
    timeframe = str(body.get("timeframe", ""))

    if not symbol or action not in ("BUY", "SELL"):
        raise HTTPException(status_code=422, detail="symbol and action (BUY/SELL) are required")

    logger.info("[TV Webhook] %s %s @ %.2f from %s (%s)", action, symbol, price, strategy, timeframe)

    # Run consensus engine to validate the TV signal
    consensus = await asyncio.get_event_loop().run_in_executor(
        None, _run_consensus, symbol
    )

    if consensus is None:
        return {"status": "skipped", "reason": "No OHLCV data available for consensus check"}

    tv_note = f"[TV:{strategy}/{timeframe}]" if timeframe else f"[TV:{strategy}]"

    # Consensus must agree with TV direction; if it HOLDs or disagrees, just log
    if consensus.action != action:
        logger.info(
            "[TV Webhook] %s consensus=%s vs TV=%s — Telegram alert sent (informational)",
            symbol, consensus.action, action,
        )
        _send_tv_alert(symbol, action, price, strategy, consensus, agreed=False)
        return {
            "status": "informed",
            "tv_action": action,
            "consensus_action": consensus.action,
            "note": "Telegram notified — consensus disagrees with TV signal",
        }

    # Consensus agrees — send HITL approval card
    _send_tv_alert(symbol, action, price, strategy, consensus, agreed=True)
    return {
        "status": "approval_sent",
        "symbol": symbol,
        "tv_action": action,
        "consensus_action": consensus.action,
        "vote_count": consensus.vote_count,
        "confidence": consensus.combined_confidence,
        "note": f"Telegram approval card sent {tv_note}",
    }


def _run_consensus(symbol: str):
    """Blocking: fetch data and run strategy engine (called in executor)."""
    try:
        import json
        from pathlib import Path
        from strategies.engine import StrategyEngine

        ohlcv: dict = {}
        cache = Path(f"data/market/{symbol}_ohlcv.json")
        if cache.exists():
            ohlcv = json.loads(cache.read_text())

        fundamentals = None
        fund_path = Path(f"data/fundamentals/{symbol}_fund.json")
        if fund_path.exists():
            fundamentals = json.loads(fund_path.read_text())

        if not ohlcv:
            return None

        engine = StrategyEngine()
        return engine.evaluate(symbol, ohlcv, fundamentals)
    except Exception as exc:
        logger.error("[TV Webhook] consensus error for %s: %s", symbol, exc)
        return None


def _send_tv_alert(symbol: str, tv_action: str, price: float, strategy: str, consensus, agreed: bool) -> None:
    """Fire-and-forget: send Telegram card for TV webhook signal."""
    try:
        from monitoring.telegram_bot import send_analysis_card, send_text
        if agreed:
            send_analysis_card(consensus)
        else:
            agree_icon = "⚠️"
            send_text(
                f"{agree_icon} <b>TradingView Alert: {tv_action} {symbol}</b> @ ₹{price:.2f}\n"
                f"Strategy: {strategy}\n"
                f"Consensus: <b>{consensus.action}</b> ({consensus.vote_count}/{consensus.total_strategies} votes)\n"
                f"<i>TV and consensus disagree — no trade proposed</i>"
            )
    except Exception as exc:
        logger.warning("[TV Webhook] Telegram send failed: %s", exc)
