import asyncio
from fastapi import APIRouter
from backend.services.data_reader import get_all_signals, get_fii_dii

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/snapshot")
async def snapshot():
    from local_db import get_portfolio_snapshot
    loop = asyncio.get_event_loop()
    snap = await loop.run_in_executor(None, get_portfolio_snapshot)
    return snap or {}


@router.get("/signals")
async def signals():
    return get_all_signals()


@router.get("/fii-dii")
async def fii_dii():
    return get_fii_dii() or {}
