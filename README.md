# LiveCam — Phase 2

A private two-way video call between two devices, direct over WebRTC — both sides see and hear each other, like a video chat. The backend only helps the two devices find each other; the actual audio/video never passes through it.

> Phase 1 (one-way laptop → phone viewer) is preserved for reference in `backend/main.py.phase1.bak`.

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
2. Enter the **same signaling server URL** and the **same room code** on both.
3. Both click **Join call**. Whoever joins first waits; whoever joins second automatically starts the connection.
4. Once connected, each side sees the other's camera (large) and their own (small, bottom-right), and audio/video streams directly peer-to-peer. The signaling server only relayed the handshake and then steps out of the way.
5. Each side can mute their mic or hide their camera independently with the controls under the call.

Rooms hold at most 2 people — a third device trying to join the same room code will be told the room is full.

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
3. Click **Join call**, and allow camera/mic access.
4. Open the same Vercel URL on the second device, enter the *same* server URL and room code, click **Join call**, and allow camera/mic access.

Within a few seconds both devices should see and hear each other.

## Notes on reliability & security (Phase 1 scope)

- **NAT traversal:** a public STUN server and a free public TURN test server (openrelay.metered.ca) are included so this works across different WiFi/cellular networks. The public TURN server is rate-limited and fine for personal testing, but swap in your own (e.g. a paid Twilio or Cloudflare TURN service) if you rely on this daily.
- **The room code is the only access control right now.** Anyone with your server URL and room code can join and watch. Keep the code private, and treat this as a personal/testing tool rather than something to expose publicly. Password protection, invite links, and authentication are natural next steps (Phase 2+ in the original roadmap) if you want to harden this.
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

This covers **Phase 1 (one-way viewer)** and **Phase 2 (two-way calls)**. Later phases in your plan — secure messaging, voice-only calls, group meetings, screen sharing/recording, and eventually a full encrypted messenger — build on this same signaling foundation. Happy to help scope any of those next.
