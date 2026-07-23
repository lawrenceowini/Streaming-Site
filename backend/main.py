"""
Signaling server for LiveCam.

This server does NOT touch video/audio/chat data. It only relays small JSON
messages (WebRTC offers, answers, and ICE candidates) between browsers so
they can negotiate direct peer-to-peer WebRTC connections. Once connected,
audio/video/chat/screen-share all flow straight between devices (or through
a TURN relay if needed) -- never through this server.

Group calls use a full mesh: every participant opens a direct WebRTC
connection to every other participant. This keeps things simple and needs
no media server, but bandwidth/CPU cost grows with each additional person,
so MAX_PEERS_PER_ROOM is kept modest. For larger meetings you'd want a real
SFU (e.g. mediasoup, Janus, or LiveKit) doing the media routing instead.

Protocol:
- Each connection is assigned a short peer_id.
- On join, the new peer gets {"type": "welcome", "peer_id": ..., "peers": [existing ids]}.
- Existing peers get {"type": "peer-joined", "peer_id": <new id>}.
- The new peer is expected to initiate a connection (offer) to each existing
  peer -- this avoids both sides racing to make an offer.
- offer/answer/ice messages must include a "to" field naming the target
  peer_id; the server adds a "from" field and relays only to that peer.
- On disconnect, remaining peers get {"type": "peer-left", "peer_id": ...}.

Optional room password: the client sends a `pwd` query parameter containing
a SHA-256 hash of the password (hashed client-side -- this server never sees
the plaintext password). Whoever creates the room (the first person in) sets
that hash as the room's password. Anyone joining afterwards must supply the
same hash or they're rejected before being added to the room. An empty pwd
means "no password".
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Optional
import json
import logging
import uuid

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

# Full mesh means each extra participant adds a connection to everyone else.
# Keep this modest -- fine for small group calls, not meant for large meetings.
MAX_PEERS_PER_ROOM = 6


class Room:
    def __init__(self, password_hash: str):
        self.password_hash = password_hash  # "" means no password
        self.peers: Dict[str, WebSocket] = {}


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
        if len(room.peers) >= MAX_PEERS_PER_ROOM:
            await websocket.send_text(json.dumps({"type": "room-full"}))
            await websocket.close()
            return
        if room.password_hash != supplied_hash:
            await websocket.send_text(json.dumps({"type": "auth-failed"}))
            await websocket.close()
            return

    my_id = uuid.uuid4().hex[:8]
    existing_ids = list(room.peers.keys())
    room.peers[my_id] = websocket
    logger.info(f"Peer {my_id} joined room '{room_code}' ({len(room.peers)} total)")

    await websocket.send_text(json.dumps({
        "type": "welcome",
        "peer_id": my_id,
        "peers": existing_ids,
    }))
    for pid, peer_ws in room.peers.items():
        if pid == my_id:
            continue
        try:
            await peer_ws.send_text(json.dumps({"type": "peer-joined", "peer_id": my_id}))
        except Exception:
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            target_id = data.get("to")
            if not target_id:
                continue
            target_ws = room.peers.get(target_id)
            if not target_ws:
                continue

            data["from"] = my_id
            try:
                await target_ws.send_text(json.dumps(data))
            except Exception:
                pass

    except WebSocketDisconnect:
        room.peers.pop(my_id, None)
        logger.info(f"Peer {my_id} left room '{room_code}' ({len(room.peers)} remaining)")
        for peer_ws in room.peers.values():
            try:
                await peer_ws.send_text(json.dumps({"type": "peer-left", "peer_id": my_id}))
            except Exception:
                pass
        if not room.peers:
            rooms.pop(room_code, None)
