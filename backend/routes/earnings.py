from fastapi import APIRouter, HTTPException
from backend.services.data_reader import get_earnings_calendar, get_earnings_results

router = APIRouter(prefix="/earnings", tags=["earnings"])


@router.get("/calendar")
async def calendar():
    return get_earnings_calendar() or {"calendar": []}


@router.get("/{symbol}/results")
async def historical_results(symbol: str):
    data = get_earnings_results(symbol.upper())
    if not data:
        raise HTTPException(status_code=404, detail=f"No results for {symbol}")
    return data
