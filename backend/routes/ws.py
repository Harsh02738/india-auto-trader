"""
WebSocket endpoint that pushes live updates to the frontend.
Clients connect to ws://localhost:8000/ws and receive JSON events:
  - "tick" every 5s: snapshot, top signals, open trades
  - real-time events from trade engine via local_db: trade_executed, trade_closed, signal
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from local_db import (
    get_latest_signals,
    get_open_trades,
    get_portfolio_snapshot,
    register_ws_listener,
    unregister_ws_listener,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

_connections: set[WebSocket] = set()


async def broadcast(message: str | dict) -> None:
    """Send a JSON message to all connected WebSocket clients."""
    if not _connections:
        return
    data = message if isinstance(message, str) else json.dumps(message)
    dead: set[WebSocket] = set()
    for ws in list(_connections):
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


async def _push_loop() -> None:
    """Background task: heartbeat every 5s + drain local_db trade event queue."""
    event_queue: asyncio.Queue = asyncio.Queue()
    register_ws_listener(event_queue)
    last_tick = 0.0
    try:
        while True:
            now = asyncio.get_event_loop().time()

            if now - last_tick >= 5:
                try:
                    loop = asyncio.get_event_loop()
                    signals = await loop.run_in_executor(None, lambda: get_latest_signals(limit=10))
                    trades  = await loop.run_in_executor(None, get_open_trades)
                    snap    = await loop.run_in_executor(None, get_portfolio_snapshot)
                    await broadcast({
                        "type":        "tick",
                        "timestamp":   datetime.now(tz=timezone.utc).isoformat(),
                        "snapshot":    snap or {},
                        "top_signals": signals,
                        "open_trades": trades,
                    })
                except Exception as exc:
                    logger.warning("WS heartbeat error: %s", exc)
                last_tick = now

            # Drain real-time trade events (already JSON strings from local_db.broadcast_event)
            try:
                while True:
                    event = event_queue.get_nowait()
                    await broadcast(event)
            except asyncio.QueueEmpty:
                pass

            await asyncio.sleep(0.5)
    finally:
        unregister_ws_listener(event_queue)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    logger.info("WS client connected. Total: %d", len(_connections))

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)
        logger.info("WS client disconnected. Total: %d", len(_connections))
