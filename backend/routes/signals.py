import asyncio
from fastapi import APIRouter, HTTPException
from local_db import get_latest_signals
from backend.services.data_reader import get_signal, get_ohlcv, get_fundamentals, get_sentiment, get_news

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
async def all_signals(limit: int = 20):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: get_latest_signals(limit=limit))


@router.get("/{symbol}")
async def signal_detail(symbol: str):
    sym = symbol.upper()
    sig   = get_signal(sym)
    ohlcv = get_ohlcv(sym)
    fund  = get_fundamentals(sym)
    sent  = get_sentiment(sym)
    news  = get_news(sym)

    if not sig and not ohlcv:
        raise HTTPException(status_code=404, detail=f"No data for {sym}")

    return {
        "signal":       sig,
        "ohlcv":        ohlcv,
        "fundamentals": fund,
        "sentiment":    sent,
        "news":         news,
    }
