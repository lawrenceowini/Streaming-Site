# LiveCam — Phase 3

A private two-way video call with encrypted text chat, direct over WebRTC, plus shareable invite links so joining is one click instead of copy-pasting a URL and room code by hand.

> Phase 1 (one-way viewer) and Phase 2 (two-way call, no chat/links) are preserved in `backend/main.py.phase1.bak` for reference — the Phase 2 signaling server also still works as-is with this Phase 3 frontend, since chat and invite links don't require any server changes.

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

1. Open the site on both devices.
2. Enter the **same signaling server URL** and the **same room code** on both — or, faster, one person clicks **Copy invite link** and sends that link to the other person. Opening it pre-fills both fields automatically.
3. Both click **Join call** (this is a deliberate click, not automatic — so you always get the browser's camera/mic permission prompt on your own terms). Whoever joins first waits; whoever joins second automatically starts the connection.
4. Once connected, each side sees the other's camera (large) and their own (small, bottom-right), with audio/video streaming directly peer-to-peer.
5. Click **💬 Chat** to open a text chat panel alongside the call. Messages travel over a WebRTC **DataChannel** — the same direct, DTLS-encrypted connection as the video — so they never pass through the signaling server either.
6. Each side can mute their mic or hide their camera independently.

Rooms hold at most 2 people — a third device trying to join the same room code will be told the room is full.

### About invite links

An invite link looks like:
```
https://your-app.vercel.app/?server=https%3A%2F%2Flivecam-signaling.onrender.com&room=amber-falcon-71
```
It just pre-fills the two setup fields — it does **not** auto-join the call. That's intentional: whoever opens the link still has to click Join call themselves, so nothing about their camera or microphone happens without a deliberate action on their end. Anyone with the link can join the room, so treat it like a room key: share it only with the person you're calling (e.g. over a messaging app you both already trust), and generate a fresh room code for each call if you want old links to stop working.

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
2. Paste in your Render **wss://** URL under "Signaling server", and generate (or enter) a room code.
3. Click **Copy invite link**, and send that link to the other person (text, email, whatever you'd normally use).
4. The other person opens the link — their fields are pre-filled — and both of you click **Join call**, allowing camera/mic access when prompted.

Within a few seconds both devices should see and hear each other, and you can open the chat panel to send text messages too.

## Notes on reliability & security (Phase 3 scope)

- **NAT traversal:** a public STUN server and a free public TURN test server (openrelay.metered.ca) are included so this works across different WiFi/cellular networks. The public TURN server is rate-limited and fine for personal testing, but swap in your own (e.g. a paid Twilio or Cloudflare TURN service) if you rely on this daily.
- **The room code (whether typed in or embedded in an invite link) is the only access control right now.** Anyone with it can join. Keep links and codes private, share them only over a channel you trust, and generate a fresh code per call if that matters to you.
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

This covers **Phase 1 (one-way viewer)**, **Phase 2 (two-way calls)**, and **Phase 3 (chat + invite links)**. Natural next steps from your original plan: real authentication/accounts instead of a shared room code, group calls (3+ people), screen sharing, recording, and eventually a full encrypted messenger. Happy to help scope any of those next.
