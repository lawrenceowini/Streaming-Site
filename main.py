"""
Signaling server for LiveCam.

This server does NOT touch video/audio data. It only relays small JSON
messages (WebRTC offers, answers, and ICE candidates) between two browsers
so they can negotiate a direct peer-to-peer WebRTC connection. Once that
connection is established, video flows straight from the laptop to the
phone (or through a TURN relay if needed) -- never through this server.

Rooms are identified by a room code in the URL: /ws/{room_code}
Anyone who connects with the same room code can signal with each other.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("livecam-signaling")

app = FastAPI(title="LiveCam Signaling Server")

# Allow the frontend (hosted on Vercel, or opened locally) to connect.
# Tighten this to your actual frontend domain once deployed if you want.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# room_code -> set of connected websockets in that room
rooms: Dict[str, Set[WebSocket]] = {}


@app.get("/")
def health_check():
    return {"status": "ok", "service": "livecam-signaling"}


@app.websocket("/ws/{room_code}")
async def signaling_endpoint(websocket: WebSocket, room_code: str):
    await websocket.accept()
    room = rooms.setdefault(room_code, set())
    room.add(websocket)
    logger.info(f"Client joined room '{room_code}' ({len(room)} total)")

    try:
        while True:
            message = await websocket.receive_text()
            # Relay the message to every other client in the same room.
            # (Phase 1 is a 1-to-1 connection, but this scales to more
            # viewers later without changing the protocol.)
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
        if not room:
            rooms.pop(room_code, None)
