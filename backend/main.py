"""
Signaling server for LiveCam.

This server does NOT touch video/audio/chat/screen-share data. It only
relays small JSON messages (WebRTC offers, answers, and ICE candidates)
between browsers so they can negotiate direct peer-to-peer WebRTC
connections. Once connected, media flows straight between devices (or
through a TURN relay if needed) -- never through this server.

Group calls use a full mesh: every participant opens a direct WebRTC
connection to every other participant. MAX_PEERS_PER_ROOM is kept modest
for that reason -- for larger meetings you'd want a real SFU (mediasoup,
Janus, LiveKit) doing the media routing instead.

Authentication: instead of a shared room password, each connecting client
must present a valid Supabase session access token (the `token` query
param). The server verifies it by asking Supabase's own auth API whether
the token is valid -- this server never handles passwords or creates
sessions itself, Supabase does. Anyone with a valid account and the room
code/server URL can join any room; there's no per-room membership list
(matches the "simplest" access model -- see README for the invite-only
alternative if you want to restrict specific rooms to specific people
later).

Required environment variables (set these in Render's dashboard, not in
this file):
  SUPABASE_URL       e.g. https://abcdefgh.supabase.co
  SUPABASE_ANON_KEY   the "anon public" key from Project Settings -> API

Protocol:
- Each connection is assigned a short peer_id.
- On join, the new peer gets
    {"type": "welcome", "peer_id": ..., "peers": [{"peer_id":..., "email":...}, ...]}
- Existing peers get {"type": "peer-joined", "peer_id": <new id>, "email": <new email>}.
- The new peer is expected to initiate a connection (offer) to each existing
  peer -- this avoids both sides racing to make an offer.
- offer/answer/ice messages must include a "to" field naming the target
  peer_id; the server adds a "from" field and relays only to that peer.
- On disconnect, remaining peers get {"type": "peer-left", "peer_id": ...}.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Optional
import httpx
import json
import logging
import os
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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

# Full mesh means each extra participant adds a connection to everyone else.
# Keep this modest -- fine for small group calls, not meant for large meetings.
MAX_PEERS_PER_ROOM = 6


class Room:
    def __init__(self):
        # peer_id -> {"ws": WebSocket, "email": str}
        self.peers: Dict[str, dict] = {}


rooms: Dict[str, Room] = {}


@app.get("/")
def health_check():
    configured = bool(SUPABASE_URL and SUPABASE_ANON_KEY)
    return {"status": "ok", "service": "livecam-signaling", "supabase_configured": configured}


async def verify_supabase_token(token: str) -> Optional[dict]:
    """Ask Supabase whether this access token is valid. Returns the user
    object (contains at least "id" and "email") if valid, else None."""
    if not token or not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {token}",
                },
            )
    except httpx.RequestError as e:
        logger.warning(f"Supabase auth check failed: {e}")
        return None

    if resp.status_code == 200:
        return resp.json()
    return None


@app.websocket("/ws/{room_code}")
async def signaling_endpoint(websocket: WebSocket, room_code: str, token: Optional[str] = ""):
    await websocket.accept()

    user = await verify_supabase_token(token or "")
    if user is None:
        await websocket.send_text(json.dumps({"type": "auth-failed"}))
        await websocket.close()
        return
    email = user.get("email", "unknown")

    room = rooms.setdefault(room_code, Room())

    if len(room.peers) >= MAX_PEERS_PER_ROOM:
        await websocket.send_text(json.dumps({"type": "room-full"}))
        await websocket.close()
        return

    my_id = uuid.uuid4().hex[:8]
    existing = [{"peer_id": pid, "email": info["email"]} for pid, info in room.peers.items()]
    room.peers[my_id] = {"ws": websocket, "email": email}
    logger.info(f"{email} joined room '{room_code}' as {my_id} ({len(room.peers)} total)")

    await websocket.send_text(json.dumps({
        "type": "welcome",
        "peer_id": my_id,
        "peers": existing,
    }))
    for pid, info in room.peers.items():
        if pid == my_id:
            continue
        try:
            await info["ws"].send_text(json.dumps({
                "type": "peer-joined",
                "peer_id": my_id,
                "email": email,
            }))
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
            target = room.peers.get(target_id)
            if not target:
                continue

            data["from"] = my_id
            try:
                await target["ws"].send_text(json.dumps(data))
            except Exception:
                pass

    except WebSocketDisconnect:
        room.peers.pop(my_id, None)
        logger.info(f"{email} left room '{room_code}' ({len(room.peers)} remaining)")
        for info in room.peers.values():
            try:
                await info["ws"].send_text(json.dumps({"type": "peer-left", "peer_id": my_id}))
            except Exception:
                pass
        if not room.peers:
            rooms.pop(room_code, None)
