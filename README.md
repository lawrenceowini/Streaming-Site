# LiveCam — Group calls, screen sharing, and real accounts

A private group video call (up to 6 people) with encrypted text chat, screen sharing, and click-to-fullscreen, direct over WebRTC — now gated behind real Supabase accounts (email + password) instead of a shared room password.

> Earlier versions (one-way viewer, two-way call, password-only, mesh-with-password) are preserved as `.bak` files in `backend/` and `frontend/` for reference.

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

1. Open the site. Log in (or sign up, if you're a new user) with an email and password.
2. Once signed in, enter the **same signaling server URL** and the **same room code** as everyone else — or, faster, one person clicks **Copy invite link** and sends that link to everyone. Opening it pre-fills both fields automatically (everyone still needs their own account to actually join).
3. Everyone clicks **Join call** (this is a deliberate click, not automatic — so you always get the browser's camera/mic permission prompt on your own terms).
4. Each new participant automatically connects directly to everyone already in the room — up to 6 people total. Everyone appears in a grid, labeled by their account email, with your own camera as one of the tiles.
5. Click **💬 Chat** to open a text chat panel. Messages are sent to everyone in the call over WebRTC **DataChannels** — the same direct, DTLS-encrypted connections as the video — so they never pass through the signaling server.
6. Click **🖥️ Share screen** to swap your outgoing video for your screen (pick a window, tab, or your whole screen). Click it again, or use the browser's own "Stop sharing" control, to switch back to your camera. Your microphone keeps working the whole time.
7. Everyone can mute their own mic or hide their own camera independently.
8. Click any tile — yours or anyone else's — to expand it to true fullscreen. This is especially handy when someone's sharing their screen and you want to read it clearly; click anywhere (or press Escape) to shrink it back to the grid.

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
It just pre-fills the two setup fields — it does **not** auto-join the call and does **not** log anyone in. That's intentional: whoever opens the link still has to sign in and click Join call themselves, so nothing about their camera or microphone happens without a deliberate action on their end. Anyone with a valid account and the link can join the room, so treat it like a room key: share it only with the people you're calling, and generate a fresh room code for each call if you want old links to stop working.

### About real accounts (replacing the room password)

Joining now requires a real Supabase account (email + password) instead of the old shared room password. A few things worth knowing:

- **The server never sees passwords.** Supabase handles sign-up, login, and password storage entirely; your browser only ever holds a short-lived **session token**, which the signaling server checks by asking Supabase's own API "is this token valid?" — it never touches or stores the password itself.
- **Access model: any signed-in account + the room code can join.** This matches the "simplest" setup — there's no per-room invite list restricting *which* accounts can join a given room, just like the old room-code model, except now every participant has a real identity (their email) instead of being anonymous. If you later want to restrict specific rooms to specific people (an actual invite list), that's a natural next step on top of this.
- **Sessions persist across visits** (Supabase's client library handles this automatically), so people don't need to log in every single time — only when their session expires or they explicitly log out.
- This is still not "enterprise-grade": there's no admin panel, no room ownership, no per-room permissions, and Supabase's free tier has its own limits (e.g. monthly active user caps) worth checking if this gets real usage.

#### One-time setup: point the app at your Supabase project

You already created a Supabase project. Two values from **Project Settings → API** need to go into the code before deploying:

1. Open `frontend/index.html`, find this near the top of the `<script>` block:
   ```js
   const SUPABASE_URL = 'https://YOUR-PROJECT.supabase.co';
   const SUPABASE_ANON_KEY = 'YOUR-ANON-PUBLIC-KEY';
   ```
   Replace both with your actual **Project URL** and **anon public** key. (The anon key is designed to be public/embedded in frontend code — it's not a secret, Supabase's database security rules are what actually protect data. It's fine for it to sit in this file.)

2. In **Render**, open your backend service → **Environment**, and add two environment variables:
   - `SUPABASE_URL` → same Project URL as above
   - `SUPABASE_ANON_KEY` → same anon key as above

   (`render.yaml` already declares these two as required — Render will prompt you to fill them in if you deploy fresh, or you can add them anytime under the service's Environment tab and it'll redeploy.)

That's it — no database tables to create, since this only uses Supabase's built-in auth, not custom data.

## 1. Deploy the backend (Render)

1. Push this repo to GitHub.
2. In [Render](https://render.com), click **New → Web Service**, connect the repo, and point it at the `backend/` folder.
3. Render should auto-detect `render.yaml`. If not, set manually:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add the two required environment variables under the service's **Environment** tab: `SUPABASE_URL` and `SUPABASE_ANON_KEY` (see "One-time setup" above).
5. Deploy. You'll get a URL like `https://livecam-signaling.onrender.com`.
   - Your WebSocket URL is the same thing with `wss://` instead of `https://`.

Note: Render's free tier spins down when idle, so the first connection after a while may take ~30-60 seconds to wake up.

## 2. Deploy the frontend (Vercel)

1. Make sure you've edited `SUPABASE_URL` and `SUPABASE_ANON_KEY` in `frontend/index.html` (see "One-time setup" above) before deploying — otherwise login will fail.
2. In [Vercel](https://vercel.com), click **New Project**, import the repo, and set the **root directory** to `frontend/`.
3. No build step needed — it's a static file. Deploy.
4. You'll get a URL like `https://livecam.vercel.app`.

## 3. Use it

1. Open the Vercel URL. Sign up with an email and password (or log in, if you've already got an account).
2. Paste in your Render **wss://** URL under "Signaling server", and generate (or enter) a room code.
3. Click **Copy invite link**, and send that link to everyone joining.
4. Each person opens the link — server and room fields are pre-filled — logs in or signs up with their own account, and clicks **Join call**, allowing camera/mic access when prompted.

Within a few seconds everyone should see and hear each other in the grid (labeled by email), and you can open the chat panel or share your screen from there.

## Notes on reliability & security

- **NAT traversal:** a public STUN server and a free public TURN test server (openrelay.metered.ca) are included so this works across different WiFi/cellular networks. The public TURN server is rate-limited and fine for personal testing, but swap in your own (e.g. a paid Twilio or Cloudflare TURN service) if you rely on this daily.
- **Access control is a real Supabase account plus the room code.** Anyone with an account and the code/URL can join any room — there's no per-room invite list yet. Keep room codes and links private, and consider a per-room allow-list as a future improvement if that matters for your use case.
- **Video, audio, and chat are all encrypted in transit** — WebRTC requires DTLS/SRTP for media and DataChannels by design, so this is on by default, not something extra that was bolted on. The signaling server never sees any of that content, only the connection setup messages needed to establish it, plus each user's email (for labeling tiles/chat) and a token it forwards to Supabase to verify.
- **HTTPS/WSS only in production** — browsers block camera access and mixed-content WebSocket connections over plain HTTP, so make sure you're using the `https://`/`wss://` URLs Render and Vercel give you.
- **The Supabase anon key is meant to be public** and is safe to leave in the frontend file — it identifies your project, it doesn't grant special access on its own. Don't confuse it with the `service_role` key (which you never need for this app, and should never expose in frontend code).

## Local testing (optional, before deploying)

Backend (needs the two Supabase environment variables set locally too):
```bash
cd backend
pip install -r requirements.txt
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_ANON_KEY=your-anon-key
uvicorn main:app --reload --port 8000
```
Then use `ws://localhost:8000` as the signaling server URL, and open `frontend/index.html` directly in two browser tabs (camera access works on `localhost` without HTTPS; make sure you've already edited the `SUPABASE_URL`/`SUPABASE_ANON_KEY` constants in that file too). Note: testing across two different devices locally requires them to reach your computer's local IP and both being HTTPS or on the same trusted network, which is why real testing is easiest once deployed.

## What's next (from the original roadmap)

This covers **Phase 1 (one-way viewer)**, **Phase 2 (two-way calls)**, **Phase 3 (chat + invite links)**, **group calls**, **screen sharing**, and **real accounts** (Supabase auth, replacing the old room password). Natural next steps from your original plan: per-room invite lists (restrict specific rooms to specific accounts), recording, a real SFU media server for larger meetings, and eventually a full encrypted messenger. Happy to help scope any of those next.
