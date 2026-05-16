from fastapi import APIRouter
from backend.services.data_reader import get_portfolio_snapshot, get_all_signals, get_fii_dii

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/snapshot")
async def snapshot():
    return get_portfolio_snapshot() or {}


@router.get("/signals")
async def signals():
    return get_all_signals()


@router.get("/fii-dii")
async def fii_dii():
    return get_fii_dii() or {}
