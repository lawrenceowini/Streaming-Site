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

Incoming-call push notifications: when a room goes from empty to having its
first participant (i.e. someone is starting a call), every other allowed
email with a registered push subscription gets a real browser push
notification -- this works even if their browser is completely closed,
since it's delivered by the browser vendor's own push service, not by this
server directly. Subscriptions are stored in `push_subscriptions` (also in
supabase_setup.sql) and managed via the /push/subscribe and
/push/unsubscribe endpoints below.

Scheduled calls: users can plan a call for a future time from the frontend
(stored directly in Supabase's `scheduled_calls` table, guarded by row-level
security so each account only sees its own rows). This server runs a
lightweight background loop (see scheduled_call_reminder_loop) that polls
that table roughly once a minute and sends the same kind of push
notification used for incoming calls once a scheduled call's time arrives.
Caveat: Render's free tier puts the service to sleep after a period of no
HTTP traffic, and a sleeping service can't run this loop -- a reminder can
therefore arrive late (whenever the service next wakes up) or, in the worst
case, not at all if it stays asleep well past the scheduled time and past
the loop's own catch-up window. An always-on (paid) instance avoids this.

Required environment variables (set these in Render's dashboard, not in
this file):
  SUPABASE_URL              e.g. https://abcdefgh.supabase.co
  SUPABASE_ANON_KEY          the "anon public" key -- used to verify user tokens
  SUPABASE_SERVICE_ROLE_KEY  the "service_role" secret key -- used to manage tables
  VAPID_PRIVATE_KEY_PEM      generated once with generate_vapid_keys.py
  VAPID_PUBLIC_KEY           generated once with generate_vapid_keys.py
  VAPID_CONTACT_EMAIL        any contact email -- required by the push spec,
                             doesn't need to be monitored

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
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
import asyncio
import httpx
import json
import logging
import os
import tempfile
import uuid

from pywebpush import webpush, WebPushException

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

VAPID_PRIVATE_KEY_PEM = os.environ.get("VAPID_PRIVATE_KEY_PEM", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CONTACT_EMAIL = os.environ.get("VAPID_CONTACT_EMAIL", "")


def _normalize_vapid_key_file(raw_pem: str) -> Optional[str]:
    """Writes a clean, correctly-formatted PEM temp file for py_vapid to
    read, instead of trusting the env var's text verbatim.

    Two very easy ways to end up with a "valid-looking" but unusable key:
      1. Pasting a multi-line PEM into an env var UI can flatten real
         newlines into literal backslash-n text, or strip them entirely.
      2. py_vapid's own PEM parsing is naive (it just slices off the first/
         last line and base64-decodes the rest) and works best with the
         "traditional"/SEC1 EC key format -- not the PKCS8 format some
         generators (including our own generate_vapid_keys.py, historically)
         produce.
    Both failures throw the exact same opaque "ASN.1 parsing error" deep
    inside pywebpush with no indication of the real cause. To sidestep both,
    we re-parse the key ourselves with `cryptography`'s own PEM loader
    (which tolerates whitespace issues far better than py_vapid's) and
    re-serialize it into the exact traditional-EC PEM format py_vapid
    expects, so what we hand it is always clean and correctly shaped."""
    if not raw_pem:
        return None
    try:
        from cryptography.hazmat.primitives import serialization as _ser

        text = raw_pem.strip()
        if "\\n" in text and "\n" not in text:
            text = text.replace("\\n", "\n")  # escaped newlines, not real ones
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"

        key_obj = _ser.load_pem_private_key(text.encode(), password=None)
        clean_pem = key_obj.private_bytes(
            encoding=_ser.Encoding.PEM,
            format=_ser.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=_ser.NoEncryption(),
        ).decode()

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
        tmp.write(clean_pem)
        tmp.close()
        return tmp.name
    except Exception as e:
        logger.error(
            f"VAPID_PRIVATE_KEY_PEM is set but could not be parsed as an EC "
            f"private key -- push notifications will silently fail until "
            f"this is fixed. Re-copy it from generate_vapid_keys.py's "
            f"output, whole block, BEGIN/END lines included. Underlying "
            f"error: {e}"
        )
        return None


# pywebpush wants a file path for the private key.
_vapid_key_file: Optional[str] = _normalize_vapid_key_file(VAPID_PRIVATE_KEY_PEM)

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
    push_configured = bool(_vapid_key_file and VAPID_PUBLIC_KEY and VAPID_CONTACT_EMAIL)
    return {
        "status": "ok",
        "service": "livecam-signaling",
        "supabase_configured": configured,
        "push_configured": push_configured,
    }


@app.get("/vapid-public-key")
def get_vapid_public_key():
    """The frontend fetches this to subscribe to push -- it's the public
    half of the VAPID key pair, safe to hand out to anyone."""
    return {"publicKey": VAPID_PUBLIC_KEY}


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


# --------------------------- Push subscriptions ---------------------------

async def upsert_push_subscription(endpoint: str, email: str, subscription: dict) -> None:
    payload = {"endpoint": endpoint, "email": email, "subscription": subscription}
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/push_subscriptions",
            json=payload,
            headers=_service_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
        )
    resp.raise_for_status()


async def delete_push_subscription(endpoint: str, email: Optional[str] = None) -> None:
    params = {"endpoint": f"eq.{endpoint}"}
    if email:
        params["email"] = f"eq.{email}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.delete(
            f"{SUPABASE_URL}/rest/v1/push_subscriptions",
            params=params,
            headers=_service_headers(),
        )
    resp.raise_for_status()


async def get_push_subscriptions_for_email(email: str) -> List[dict]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/push_subscriptions",
            params={"email": f"eq.{email}", "select": "*"},
            headers=_service_headers(),
        )
    resp.raise_for_status()
    return resp.json()


def _send_one_push(subscription: dict, payload: dict) -> Optional[str]:
    """Blocking call (pywebpush uses requests under the hood), meant to be
    run via asyncio.to_thread. Returns the subscription's endpoint if it
    turned out to be expired/invalid and should be deleted, else None."""
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=_vapid_key_file,
            vapid_claims={"sub": f"mailto:{VAPID_CONTACT_EMAIL}"},
        )
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            return subscription.get("endpoint")
        logger.warning(f"Push send failed: {e}")
    return None


async def send_message_push(to_email: str, from_email: str, conversation_id: str, preview: str) -> None:
    if not (_vapid_key_file and VAPID_PUBLIC_KEY and VAPID_CONTACT_EMAIL):
        return  # push isn't configured -- silently skip
    payload = {
        "kind": "message",
        "title": from_email,
        "body": preview[:120],  # keep notifications short, like every other messenger does
        "conversation_id": conversation_id,
        "from": from_email,
    }
    try:
        rows = await get_push_subscriptions_for_email(to_email)
    except httpx.HTTPError as e:
        logger.warning(f"Could not fetch push subscriptions for {to_email}: {e}")
        return
    for row in rows:
        expired_endpoint = await asyncio.to_thread(_send_one_push, row["subscription"], payload)
        if expired_endpoint:
            logger.info(f"Push subscription for {to_email} is expired/invalid -- removing it.")
            try:
                await delete_push_subscription(expired_endpoint)
            except httpx.HTTPError:
                pass
        else:
            logger.info(f"Sent message push to {to_email}")


async def send_incoming_call_push(to_emails: List[str], room_code: str, caller_email: str) -> None:
    if not (_vapid_key_file and VAPID_PUBLIC_KEY and VAPID_CONTACT_EMAIL):
        return  # push isn't configured -- silently skip rather than error the call
    payload = {
        "title": f"Incoming call from {caller_email}",
        "body": "Tap to join the call.",
        "room_code": room_code,
        "from": caller_email,
    }
    for email in to_emails:
        try:
            rows = await get_push_subscriptions_for_email(email)
        except httpx.HTTPError as e:
            logger.warning(f"Could not fetch push subscriptions for {email}: {e}")
            continue
        for row in rows:
            expired_endpoint = await asyncio.to_thread(_send_one_push, row["subscription"], payload)
            if expired_endpoint:
                logger.info(
                    f"Push subscription for {email} is expired/invalid (FCM/APNs "
                    f"returned 404/410) -- removing it. They'll need to click "
                    f"'Enable call alerts' again on that device."
                )
                try:
                    await delete_push_subscription(expired_endpoint)
                except httpx.HTTPError:
                    pass
            else:
                logger.info(f"Sent incoming-call push to {email}")


async def send_scheduled_call_push(to_emails: List[str], room_code: str, title: str) -> None:
    if not (_vapid_key_file and VAPID_PUBLIC_KEY and VAPID_CONTACT_EMAIL):
        return  # push isn't configured -- silently skip rather than error
    payload = {
        "title": f"Scheduled call: {title}",
        "body": "It's time -- tap to join.",
        "room_code": room_code,
    }
    for email in to_emails:
        try:
            rows = await get_push_subscriptions_for_email(email)
        except httpx.HTTPError as e:
            logger.warning(f"Could not fetch push subscriptions for {email}: {e}")
            continue
        for row in rows:
            expired_endpoint = await asyncio.to_thread(_send_one_push, row["subscription"], payload)
            if expired_endpoint:
                logger.info(
                    f"Push subscription for {email} is expired/invalid -- removing it."
                )
                try:
                    await delete_push_subscription(expired_endpoint)
                except httpx.HTTPError:
                    pass
            else:
                logger.info(f"Sent scheduled-call push to {email}")


# How far back we're still willing to send a "just missed it" reminder for --
# beyond this, a scheduled call is treated as stale (the service was probably
# asleep) and is just marked notified without sending anything.
SCHEDULED_CALL_CATCHUP_WINDOW = timedelta(minutes=30)
SCHEDULED_CALL_POLL_INTERVAL_SECONDS = 30


async def get_due_scheduled_calls() -> List[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - SCHEDULED_CALL_CATCHUP_WINDOW
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/scheduled_calls",
            params=[
                ("notified", "eq.false"),
                ("scheduled_at", f"lte.{now.isoformat()}"),
                ("scheduled_at", f"gte.{cutoff.isoformat()}"),
                ("select", "*"),
            ],
            headers=_service_headers(),
        )
    resp.raise_for_status()
    return resp.json()


async def mark_scheduled_call_notified(row_id: str) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/scheduled_calls",
            params={"id": f"eq.{row_id}"},
            json={"notified": True},
            headers=_service_headers(),
        )
    resp.raise_for_status()


async def scheduled_call_reminder_loop() -> None:
    """Polls scheduled_calls for anything due and pushes a reminder to its
    invited emails. See the module docstring for the Render-sleep caveat --
    this only runs while the process is actually awake."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return  # not configured -- nothing to poll
    while True:
        try:
            due = await get_due_scheduled_calls()
            for row in due:
                emails = list(row.get("invited_emails") or [])
                owner_email = row.get("owner_email")
                if owner_email and owner_email not in emails:
                    emails.append(owner_email)
                if emails:
                    await send_scheduled_call_push(
                        emails, row["room_code"], row.get("title") or "Call"
                    )
                await mark_scheduled_call_notified(row["id"])
                logger.info(f"Sent scheduled-call reminder for room '{row['room_code']}'")
        except httpx.HTTPError as e:
            logger.warning(f"Scheduled-call reminder poll failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in scheduled-call reminder loop: {e}")
        await asyncio.sleep(SCHEDULED_CALL_POLL_INTERVAL_SECONDS)


@app.on_event("startup")
async def _start_background_tasks():
    asyncio.create_task(scheduled_call_reminder_loop())


class PushSubscribeBody(BaseModel):
    token: str
    subscription: dict


class PushUnsubscribeBody(BaseModel):
    token: str
    endpoint: str


@app.post("/push/subscribe")
async def push_subscribe(body: PushSubscribeBody):
    user = await verify_supabase_token(body.token)
    if user is None:
        return {"error": "invalid session"}, 401
    email = (user.get("email") or "").lower()
    endpoint = body.subscription.get("endpoint")
    if not endpoint:
        return {"error": "malformed subscription"}, 400
    await upsert_push_subscription(endpoint, email, body.subscription)
    return {"status": "subscribed"}


@app.post("/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubscribeBody):
    user = await verify_supabase_token(body.token)
    if user is None:
        return {"error": "invalid session"}, 401
    email = (user.get("email") or "").lower()
    await delete_push_subscription(body.endpoint, email=email)
    return {"status": "unsubscribed"}


class NotifyMessageBody(BaseModel):
    token: str
    to_email: str
    conversation_id: str
    preview: str


@app.post("/push/notify-message")
async def push_notify_message(body: NotifyMessageBody):
    """Called by the frontend right after it inserts a chat message into
    Supabase, to push a notification to the recipient. The message itself
    is written directly to Supabase (see supabase_setup.sql) -- this
    endpoint's only job is the notification, so it needs no message content
    beyond a short preview, and can't read or store the conversation."""
    user = await verify_supabase_token(body.token)
    if user is None:
        return {"error": "invalid session"}, 401
    from_email = (user.get("email") or "").lower()
    to_email = (body.to_email or "").lower()
    if not to_email or not body.conversation_id:
        return {"error": "missing to_email or conversation_id"}, 400
    await send_message_push(to_email, from_email, body.conversation_id, body.preview or "")
    return {"status": "sent"}


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

    is_starting_the_call = len(room.peers) == 0  # this room was empty until now

    my_id = uuid.uuid4().hex[:8]
    existing = [{"peer_id": pid, "email": info["email"]} for pid, info in room.peers.items()]
    room.peers[my_id] = {"ws": websocket, "email": email}
    logger.info(f"{email} joined room '{room_code}' as {my_id} ({len(room.peers)} total)")

    if is_starting_the_call:
        allowed = set(access["allowed_emails"] or []) | {access["owner_email"]}
        notify_emails = [e for e in allowed if e != email]
        if notify_emails:
            # Fire-and-forget: don't make the caller wait on push delivery to
            # other people's devices before their own connection proceeds.
            asyncio.create_task(send_incoming_call_push(notify_emails, room_code, email))

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
