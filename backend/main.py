"""
Signaling server for LiveCam.

This server does NOT touch video/audio/chat data. It only relays small JSON
messages (WebRTC offers, answers, and ICE candidates) between two browsers
so they can negotiate a direct peer-to-peer WebRTC connection. Once that
connection is established, audio/video/chat flow straight between the two
devices (or through a TURN relay if needed) -- never through this server.

Rooms are identified by a room code in the URL: /ws/{room_code}
Each room holds at most 2 people. Whoever connects first is told they're
"first" (they wait for an offer); whoever connects second is told they're
"second" (they create the offer).

Optional room password: the client sends a `pwd` query parameter containing
a SHA-256 hash of the password (hashed client-side -- this server never sees
the plaintext password). Whoever creates the room (the "first" person) sets
that hash as the room's password. Anyone joining afterwards must supply the
same hash or they're rejected before being added to the room. An empty pwd
means "no password" and behaves exactly like before.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set, Optional
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


class Room:
    def __init__(self, password_hash: str):
        self.password_hash = password_hash  # "" means no password
        self.sockets: Set[WebSocket] = set()


rooms: Dict[str, Room] = {}


@app.get("/")
def health_check():
    return {"status": "ok", "service": "livecam-signaling"}


@app.websocket("/ws/{room_code}")
async def signaling_endpoint(websocket: WebSocket, room_code: str, pwd: Optional[str] = ""):
    await websocket.accept()
    supplied_hash = pwd or ""

    room = rooms.get(room_code)

    if room is None:
        # First person in -- this establishes the room's password (may be empty).
        room = Room(password_hash=supplied_hash)
        rooms[room_code] = room
    else:
        if len(room.sockets) >= MAX_PEERS_PER_ROOM:
            await websocket.send_text(json.dumps({"type": "room-full"}))
            await websocket.close()
            return
        if room.password_hash != supplied_hash:
            await websocket.send_text(json.dumps({"type": "auth-failed"}))
            await websocket.close()
            return

    role = "first" if len(room.sockets) == 0 else "second"
    room.sockets.add(websocket)
    logger.info(f"Client joined room '{room_code}' as '{role}' ({len(room.sockets)} total)")

    await websocket.send_text(json.dumps({"type": "welcome", "role": role}))
    for peer in room.sockets:
        if peer is not websocket:
            try:
                await peer.send_text(json.dumps({"type": "peer-joined"}))
            except Exception:
                pass

    try:
        while True:
            message = await websocket.receive_text()
            dead = []
            for peer in room.sockets:
                if peer is websocket:
                    continue
                try:
                    await peer.send_text(message)
                except Exception:
                    dead.append(peer)
            for peer in dead:
                room.sockets.discard(peer)

    except WebSocketDisconnect:
        room.sockets.discard(websocket)
        logger.info(f"Client left room '{room_code}' ({len(room.sockets)} remaining)")
        for peer in room.sockets:
            try:
                await peer.send_text(json.dumps({"type": "peer-left"}))
            except Exception:
                pass
        if not room.sockets:
            rooms.pop(room_code, None)
