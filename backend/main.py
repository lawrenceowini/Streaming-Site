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

Authentication: each connecting client must present a valid Supabase
session access token (the `token` query param), verified against
Supabase's own auth API. This server never handles passwords itself.

Room access control: on top of authentication, each room has an owner
(whoever created it -- the first person to ever connect with that room
code) and an allow-list of emails. Only the owner and allowed emails may
join. This is stored in a small Supabase table (`room_access`, see
backend/supabase_setup.sql) reached through the service_role key, which
bypasses Row Level Security -- that key must stay server-side only, never
in frontend code.

Required environment variables (set these in Render's dashboard, not in
this file):
  SUPABASE_URL              e.g. https://abcdefgh.supabase.co
  SUPABASE_ANON_KEY          the "anon public" key -- used to verify user tokens
  SUPABASE_SERVICE_ROLE_KEY  the "service_role" secret key -- used to manage room_access

Protocol additions over the plain-auth version:
- The connecting client may include an `invited` query param: a comma-
  separated list of emails. This only has an effect for the room's owner
  -- when the owner connects with a non-empty `invited` list, those emails
  are added to the room's allow-list (existing entries are kept, not
  replaced). Anyone else's `invited` param is ignored.
- If a room code has never been used before, the first person to connect
  becomes its owner, and their own email is automatically allowed.
- Anyone who is neither the owner nor on the allow-list gets
  {"type": "not-invited"} and the connection is closed.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, List, Optional
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
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Full mesh means each extra participant adds a connection to everyone else.
# Keep this modest -- fine for small group calls, not meant for large meetings.
MAX_PEERS_PER_ROOM = 6


class Room:
    """In-memory record of who's currently connected to a room's signaling
    (separate from room_access, which is the persistent DB record of who's
    *allowed* to connect)."""
    def __init__(self):
        # peer_id -> {"ws": WebSocket, "email": str}
        self.peers: Dict[str, dict] = {}


rooms: Dict[str, Room] = {}


@app.get("/")
def health_check():
    configured = bool(SUPABASE_URL and SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY)
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


def _service_headers(extra: Optional[dict] = None) -> dict:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


async def get_room_access(room_code: str) -> Optional[dict]:
    """Fetch the room_access row for this room code, or None if it doesn't exist yet."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/room_access",
            params={"room_code": f"eq.{room_code}", "select": "*"},
            headers=_service_headers(),
        )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def create_room_access(room_code: str, owner_email: str, allowed_emails: List[str]) -> dict:
    """Create the room_access row for a brand-new room. The owner is always
    included in allowed_emails automatically."""
    payload = {
        "room_code": room_code,
        "owner_email": owner_email,
        "allowed_emails": sorted(set(allowed_emails) | {owner_email}),
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/room_access",
            json=payload,
            headers=_service_headers({"Prefer": "return=representation"}),
        )
    resp.raise_for_status()
    return resp.json()[0]


async def update_allowed_emails(room_code: str, allowed_emails: List[str]) -> None:
    """Overwrite the allow-list for a room (caller is responsible for merging
    with the existing list first, so this never silently removes access)."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/room_access",
            params={"room_code": f"eq.{room_code}"},
            json={"allowed_emails": sorted(set(allowed_emails))},
            headers=_service_headers({"Prefer": "return=minimal"}),
        )
    resp.raise_for_status()


def parse_invited_list(raw: str) -> List[str]:
    if not raw:
        return []
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


@app.websocket("/ws/{room_code}")
async def signaling_endpoint(
    websocket: WebSocket,
    room_code: str,
    token: Optional[str] = "",
    invited: Optional[str] = "",
):
    await websocket.accept()

    user = await verify_supabase_token(token or "")
    if user is None:
        await websocket.send_text(json.dumps({"type": "auth-failed"}))
        await websocket.close()
        return
    email = (user.get("email") or "").lower()
    invited_list = parse_invited_list(invited or "")

    try:
        access = await get_room_access(room_code)
        if access is None:
            # First person to ever use this room code becomes its owner.
            access = await create_room_access(room_code, owner_email=email, allowed_emails=invited_list)
            logger.info(f"{email} created room '{room_code}' (owner)")
        else:
            is_owner = access["owner_email"] == email
            if is_owner and invited_list:
                merged = set(access["allowed_emails"] or []) | set(invited_list) | {email}
                await update_allowed_emails(room_code, list(merged))
                access["allowed_emails"] = list(merged)

            allowed = set(access["allowed_emails"] or []) | {access["owner_email"]}
            if email not in allowed:
                await websocket.send_text(json.dumps({"type": "not-invited"}))
                await websocket.close()
                return
    except httpx.HTTPError as e:
        logger.error(f"Supabase room_access check failed: {e}")
        await websocket.send_text(json.dumps({"type": "auth-failed"}))
        await websocket.close()
        return

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
