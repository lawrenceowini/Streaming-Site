# LiveCam — Group calls + screen sharing

A private group video call (up to 6 people) with encrypted text chat and screen sharing, direct over WebRTC, plus shareable invite links and an optional room password.

> Earlier versions (one-way viewer, two-way call, password-only) are preserved as `.bak` files in `backend/` and `frontend/` for reference.

```
livecam/
  backend/          FastAPI + WebSocket signaling server
    main.py
    requirements.txt
    render.yaml
  frontend/          Single-page app (broadcaster + viewer in one file)
    index.html
    vercel.json
```

## How it works

1. Open the site on each device joining the call.
2. Enter the **same signaling server URL** and the **same room code** on all of them — or, faster, one person clicks **Copy invite link** and sends that link to everyone. Opening it pre-fills both fields automatically.
3. Everyone clicks **Join call** (this is a deliberate click, not automatic — so you always get the browser's camera/mic permission prompt on your own terms).
4. Each new participant automatically connects directly to everyone already in the room — up to 6 people total. Everyone appears in a grid, with your own camera as one of the tiles.
5. Click **💬 Chat** to open a text chat panel. Messages are sent to everyone in the call over WebRTC **DataChannels** — the same direct, DTLS-encrypted connections as the video — so they never pass through the signaling server.
6. Click **🖥️ Share screen** to swap your outgoing video for your screen (pick a window, tab, or your whole screen). Click it again, or use the browser's own "Stop sharing" control, to switch back to your camera. Your microphone keeps working the whole time.
7. Everyone can mute their own mic or hide their own camera independently.

### About group calls (mesh, not a media server)

This uses a **full mesh**: your device opens a direct WebRTC connection to every other participant. That's simple and needs no extra infrastructure, but each additional person means more outgoing bandwidth and CPU for everyone already in the call — it works well for small groups (a handful of people) but doesn't scale to large meetings. Rooms are capped at 6 people for that reason. If you outgrow this, the natural next step (from the original roadmap) is a real SFU media server like **mediasoup**, **Janus**, or **LiveKit**, which routes media through a single server instead of everyone connecting to everyone.

### About screen sharing

Screen sharing works by swapping the video track your camera was sending for a track from your screen (`getDisplayMedia`), without needing to reconnect — so it should feel instant. A few practical notes:
- Works reliably on desktop Chrome, Firefox, and Edge. Support on mobile browsers is inconsistent (some recent Android/Chrome versions support it; iOS Safari's support is limited), so treat this as primarily a desktop feature for now.
- Only your video is swapped — audio always comes from your microphone, not your screen/system audio.
- If you also have your camera "hidden," turning that back on while screen sharing won't do anything visible until you stop sharing, since the screen feed takes priority on the video track.

### About invite links

An invite link looks like:
```
https://your-app.vercel.app/?server=https%3A%2F%2Flivecam-signaling.onrender.com&room=amber-falcon-71
```
It just pre-fills the two setup fields — it does **not** auto-join the call, and it does **not** include the room password (see below). That's intentional: whoever opens the link still has to click Join call themselves, so nothing about their camera or microphone happens without a deliberate action on their end. Anyone with the link can join the room, so treat it like a room key: share it only with the people you're calling, and generate a fresh room code for each call if you want old links to stop working.

### About the room password

There's an optional "Room password" field. Whoever joins a room **first** sets its password for that call (leave it blank for no password). Anyone joining after that must enter the same password or the server rejects them before they're let into the room.

A few things worth knowing:
- The server only ever sees a **SHA-256 hash** of the password, computed in the browser — never the plaintext.
- The password is deliberately **not** part of the invite link. The link is for convenience; the password is a second factor you share a different way (say it out loud, send it in a separate message) so that a leaked or forwarded link by itself isn't enough to get in.
- The password lives only in memory on the signaling server for as long as the room is occupied. Once everyone leaves, the room (and its password) is forgotten — the next person to use that room code sets a new one.
- This is a lightweight deterrent, not enterprise-grade auth: there's no rate-limiting on password attempts yet, and the room code + password model is still simpler than real user accounts. Good enough to keep casual/unwanted joins out; if you need stronger guarantees later, real authentication (from the original roadmap) is the next step up.

## 1. Deploy the backend (Render)

1. Push this repo to GitHub.
2. In [Render](https://render.com), click **New → Web Service**, connect the repo, and point it at the `backend/` folder.
3. Render should auto-detect `render.yaml`. If not, set manually:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Deploy. You'll get a URL like `https://livecam-signaling.onrender.com`.
   - Your WebSocket URL is the same thing with `wss://` instead of `https://`.

Note: Render's free tier spins down when idle, so the first connection after a while may take ~30-60 seconds to wake up.

## 2. Deploy the frontend (Vercel)

1. In [Vercel](https://vercel.com), click **New Project**, import the repo, and set the **root directory** to `frontend/`.
2. No build step needed — it's a static file. Deploy.
3. You'll get a URL like `https://livecam.vercel.app`.

## 3. Use it

1. Open the Vercel URL on the first device.
2. Paste in your Render **wss://** URL under "Signaling server", generate (or enter) a room code, and optionally set a room password.
3. Click **Copy invite link**, and send that link to everyone joining. Share the password separately, if you set one.
4. Each person opens the link — server and room fields are pre-filled — enters the password if there is one, and clicks **Join call**, allowing camera/mic access when prompted.

Within a few seconds everyone should see and hear each other in the grid, and you can open the chat panel or share your screen from there.

## Notes on reliability & security

- **NAT traversal:** a public STUN server and a free public TURN test server (openrelay.metered.ca) are included so this works across different WiFi/cellular networks. The public TURN server is rate-limited and fine for personal testing, but swap in your own (e.g. a paid Twilio or Cloudflare TURN service) if you rely on this daily.
- **Access control is the room code plus an optional password.** Both are shared secrets, not real authentication — there are no accounts. Keep links, codes, and passwords private, share them over channels you trust, and generate a fresh room code (and password) per call if that matters to you.
- **Video, audio, and chat are all encrypted in transit** — WebRTC requires DTLS/SRTP for media and DataChannels by design, so this is on by default, not something extra that was bolted on. The signaling server never sees any of that content, only the connection setup messages needed to establish it.
- **HTTPS/WSS only in production** — browsers block camera access and mixed-content WebSocket connections over plain HTTP, so make sure you're using the `https://`/`wss://` URLs Render and Vercel give you.

## Local testing (optional, before deploying)

Backend:
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Then use `ws://localhost:8000` as the signaling server URL, and open `frontend/index.html` directly in two browser tabs (camera access works on `localhost` without HTTPS). Note: testing laptop→phone locally requires your phone to reach your laptop's local IP and both being HTTPS or on the same trusted network, which is why real testing is easiest once deployed.

## What's next (from the original roadmap)

This covers **Phase 1 (one-way viewer)**, **Phase 2 (two-way calls)**, **Phase 3 (chat + invite links)**, room passwords, **group calls**, and **screen sharing**. Natural next steps from your original plan: recording, a real SFU media server for larger meetings, real authentication/accounts instead of a shared room code, and eventually a full encrypted messenger. Happy to help scope any of those next.
