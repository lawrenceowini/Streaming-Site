# LiveCam — Phase 1

Stream one device's camera (e.g. your laptop) to another (e.g. your phone) in real time, directly over WebRTC. The backend only helps the two devices find each other; the actual video never passes through it.

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

1. Open the site on your laptop, pick **Broadcast**, and start the camera.
2. Open the same site on your phone, pick **View**, and connect.
3. Both devices must use the **same signaling server URL** and the **same room code**.
4. Once connected, video streams peer-to-peer. The signaling server just relayed a handshake and then steps out of the way.

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

1. Open the Vercel URL on your laptop.
2. Paste in your Render **wss://** URL under "Signaling server", or generate/enter a room code.
3. Click **Broadcast** → **Start broadcasting**, and allow camera/mic access.
4. Open the same Vercel URL on your phone, enter the *same* server URL and room code, click **View** → **Connect**.

You should see your laptop's camera feed on your phone within a few seconds.

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

This covers **Phase 1: laptop webcam → phone viewer**. Later phases in your plan — two-way video, authentication/rooms, messaging, group calls, screen sharing/recording, and eventually a full encrypted messenger — build on this same signaling foundation. Happy to help scope any of those next.
