"""
Signaling server for LiveCam.

This server does NOT touch video/audio data. It only relays small JSON
messages (WebRTC offers, answers, and ICE candidates) between two browsers
so they can negotiate a direct peer-to-peer WebRTC connection. Once that
connection is established, audio/video flows straight between the two
devices (or through a TURN relay if needed) -- never through this server.

Phase 2: two-way calls. Each room holds at most 2 people. Whoever connects
first is told they're "first" (they wait for an offer); whoever connects
second is told they're "second" (they create the offer). This avoids both
sides trying to initiate the call at once.

Rooms are identified by a room code in the URL: /ws/{room_code}
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("livecam-signaling")

app = FastAPI(title="LiveCam Signaling Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_PEERS_PER_ROOM = 2

# room_code -> set of connected websockets in that room
rooms: Dict[str, Set[WebSocket]] = {}


@app.get("/")
def health_check():
    return {"status": "ok", "service": "livecam-signaling"}


@app.websocket("/ws/{room_code}")
async def signaling_endpoint(websocket: WebSocket, room_code: str):
    await websocket.accept()
    room = rooms.setdefault(room_code, set())

    if len(room) >= MAX_PEERS_PER_ROOM:
        await websocket.send_text(json.dumps({"type": "room-full"}))
        await websocket.close()
        return

    role = "first" if len(room) == 0 else "second"
    room.add(websocket)
    logger.info(f"Client joined room '{room_code}' as '{role}' ({len(room)} total)")

    # Tell this client its role, and tell any existing peer that someone joined.
    await websocket.send_text(json.dumps({"type": "welcome", "role": role}))
    for peer in room:
        if peer is not websocket:
            try:
                await peer.send_text(json.dumps({"type": "peer-joined"}))
            except Exception:
                pass

    try:
        while True:
            message = await websocket.receive_text()
            # Relay signaling messages (offer/answer/ice) to the other peer.
            dead = []
            for peer in room:
                if peer is websocket:
                    continue
                try:
                    await peer.send_text(message)
                except Exception:
                    dead.append(peer)
            for peer in dead:
                room.discard(peer)

    except WebSocketDisconnect:
        room.discard(websocket)
        logger.info(f"Client left room '{room_code}' ({len(room)} remaining)")
        for peer in room:
            try:
                await peer.send_text(json.dumps({"type": "peer-left"}))
            except Exception:
                pass
        if not room:
            rooms.pop(room_code, None)
