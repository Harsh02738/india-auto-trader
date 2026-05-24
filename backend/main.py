"""
FastAPI backend for India Auto-Trader.
Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from backend.routes import portfolio, signals, options, earnings, penny, trades, pnl, ws, intraday
from backend.routes.ws import _push_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("backend.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — local_db initialises itself on import (SQLite CREATE TABLE IF NOT EXISTS)
    import local_db  # noqa: F401 — triggers _init() to create tables
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
app.include_router(intraday.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
