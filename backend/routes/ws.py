"""
WebSocket endpoint that pushes live updates to the frontend.
Clients connect to ws://localhost:8000/ws and receive JSON updates every 5 seconds.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.services.data_reader import (
    get_portfolio_snapshot,
    get_all_signals,
    get_market_pcr,
    get_fii_dii,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

_connections: set[WebSocket] = set()


async def broadcast(message: dict) -> None:
    """Send a JSON message to all connected WebSocket clients."""
    if not _connections:
        return
    dead: set[WebSocket] = set()
    data = json.dumps(message)
    for ws in list(_connections):
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


async def _push_loop() -> None:
    """Background task: push updates every 5 seconds."""
    while True:
        try:
            snapshot = get_portfolio_snapshot() or {}
            signals  = get_all_signals()[:10]
            pcr      = get_market_pcr() or {}
            fii      = get_fii_dii() or {}

            await broadcast({
                "type":      "tick",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "snapshot":  snapshot,
                "top_signals": signals,
                "pcr":       pcr,
                "fii_dii":   {
                    "fii_net": fii.get("fii_net_today"),
                    "dii_net": fii.get("dii_net_today"),
                    "signal":  fii.get("signal_today"),
                },
            })
        except Exception as exc:
            logger.warning("WS push error: %s", exc)

        await asyncio.sleep(5)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    logger.info("WS client connected. Total: %d", len(_connections))

    try:
        while True:
            # Keep alive — receive any ping/pong or close from client
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)
        logger.info("WS client disconnected. Total: %d", len(_connections))
