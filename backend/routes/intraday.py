"""
Intraday 1-minute bar data for the frontend chart.
Reads from data/realtime/{symbol}_1m.json written by KotakRealtimeCollector.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

_IST = ZoneInfo("Asia/Kolkata")

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

    # File is a dict written by KotakRealtimeCollector; candles are under "candles" key
    if isinstance(data, dict):
        raw = data.get("candles", [])
        parsed = []
        for b in raw:
            if not all(k in b for k in ("t", "o", "h", "l", "c", "v")):
                continue
            try:
                # "t" is "2026-05-25T09:21" — parse as IST, convert to UTC unix seconds
                ts = int(datetime.fromisoformat(b["t"]).replace(tzinfo=_IST).timestamp())
            except Exception:
                continue
            parsed.append({
                "time": ts, "open": b["o"], "high": b["h"],
                "low": b["l"], "close": b["c"], "volume": b["v"],
            })
        data = parsed

    # Deduplicate by timestamp and sort ascending (lightweight-charts requires this)
    seen_ts: set[int] = set()
    clean = []
    for b in sorted(data, key=lambda x: x["time"]):
        if b["time"] not in seen_ts:
            seen_ts.add(b["time"])
            clean.append(b)

    return clean[-bars:]


@router.get("")
async def list_intraday_symbols():
    """Return all symbols that have 1-min data files."""
    if not _DATA_DIR.exists():
        return []
    symbols = [p.stem.replace("_1m", "") for p in _DATA_DIR.glob("*_1m.json")]
    return sorted(symbols)
