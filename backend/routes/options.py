from fastapi import APIRouter, HTTPException
from backend.services.data_reader import get_option_chain, get_oi_data, get_market_pcr

router = APIRouter(prefix="/options", tags=["options"])


@router.get("/pcr")
async def market_pcr():
    return get_market_pcr() or {}


@router.get("/{symbol}/chain")
async def option_chain(symbol: str):
    data = get_option_chain(symbol.upper())
    if not data:
        raise HTTPException(status_code=404, detail=f"No option chain for {symbol}")
    return data


@router.get("/{symbol}/oi")
async def oi_data(symbol: str):
    data = get_oi_data(symbol.upper())
    if not data:
        raise HTTPException(status_code=404, detail=f"No OI data for {symbol}")
    return data
