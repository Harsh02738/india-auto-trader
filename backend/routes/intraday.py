"""
Intraday 1-minute bar data for the frontend chart.
Reads from data/realtime/{symbol}_1m.json written by KotakRealtimeCollector.
"""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/intraday", tags=["intraday"])

_DATA_DIR = Path("data/realtime")


@router.get("/{symbol}")
async def get_intraday(symbol: str, bars: int = 390):
    sym = symbol.upper()
    path = _DATA_DIR / f"{sym}_1m.json"

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No intraday data for {sym}")

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: json.loads(path.read_text()))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Read error: {exc}")

    return data[-bars:] if isinstance(data, list) else data


@router.get("")
async def list_intraday_symbols():
    """Return all symbols that have 1-min data files."""
    if not _DATA_DIR.exists():
        return []
    symbols = [p.stem.replace("_1m", "") for p in _DATA_DIR.glob("*_1m.json")]
    return sorted(symbols)
