import asyncio
from fastapi import APIRouter, HTTPException
from local_db import get_all_trades, get_open_trades

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("")
async def list_trades(limit: int = 50, open_only: bool = False):
    loop = asyncio.get_event_loop()
    if open_only:
        return await loop.run_in_executor(None, get_open_trades)
    return await loop.run_in_executor(None, lambda: get_all_trades(limit=limit))


@router.get("/{trade_id}")
async def get_trade(trade_id: int):
    loop = asyncio.get_event_loop()
    trades = await loop.run_in_executor(None, lambda: get_all_trades(limit=10000))
    for t in trades:
        if t.get("id") == trade_id:
            return t
    raise HTTPException(status_code=404, detail="Trade not found")
