# LiveCam — Group calls, screen sharing, real accounts, invite-only rooms, and incoming-call notifications

A private group video call (up to 6 people) with encrypted text chat, file sharing, screen sharing, and click-to-fullscreen, direct over WebRTC — gated behind real Supabase accounts, with each room restricted to specifically invited emails, and real push notifications when someone starts a call, even if your browser is closed.

> Earlier versions are preserved as `.bak` files in `backend/` and `frontend/` for reference.

```
livecam/
  backend/
    main.py
    requirements.txt
    render.yaml
    supabase_setup.sql       run once in Supabase's SQL Editor
    generate_vapid_keys.py   run once locally to create your push notification keys
  frontend/
    index.html
    sw.js                    service worker: receives pushes, handles notification clicks
    vercel.json
```

## How it works

1. Open the site. Log in (or sign up, if you're a new user) with an email and password.
2. The first time, enter your signaling server URL — it's saved to your account automatically after that, so you won't need to type it again on future visits or other devices you log into. (Opening someone else's invite link still overrides it for that session, since it points at whichever server *they're* using.)
3. Enter the **same room code** as everyone else — or, faster, one person clicks **Copy invite link** and sends that link to everyone. Opening it pre-fills the room field automatically (everyone still needs their own account, and needs to actually be allowed into the room — see below).
4. Everyone clicks **Join call** (this is a deliberate click, not automatic — so you always get the browser's camera/mic permission prompt on your own terms). Check **"Join audio only"** first if you'd rather start without sending your camera — you can turn it on or off anytime during the call, no need to decide upfront.
5. Each new participant automatically connects directly to everyone already in the room — up to 6 people total. Everyone appears in a grid, labeled by their account email, with your own camera as one of the tiles. Anyone who's currently audio-only shows as an avatar circle with their initial instead of a video feed.
6. Click **💬 Chat** to open a text chat panel. Messages are sent to everyone in the call over WebRTC **DataChannels** — the same direct, DTLS-encrypted connections as the video — so they never pass through the signaling server. Click **📎** next to the chat box to send a file the same way — images show a thumbnail preview, everything else shows a name and download link, with a live "Sending…/Receiving… X%" progress readout while it's in flight.
7. Click **🖥️ Share screen** to swap your outgoing video for your screen (pick a window, tab, or your whole screen). Click it again, or use the browser's own "Stop sharing" control, to switch back to your camera. Your microphone keeps working the whole time.
8. Everyone can mute their own mic or hide their own camera independently.
9. Click any tile — yours or anyone else's — to expand it to true fullscreen. This is especially handy when someone's sharing their screen and you want to read it clearly; click anywhere (or press Escape) to shrink it back to the grid.
10. Click **🔔 Enable call alerts** once (in the account bar) to get a real notification when someone starts a call in a room you're invited to — this works even if your browser is completely closed. See below for details.

### About group calls (mesh, not a media server)

This uses a **full mesh**: your device opens a direct WebRTC connection to every other participant. That's simple and needs no extra infrastructure, but each additional person means more outgoing bandwidth and CPU for everyone already in the call — it works well for small groups (a handful of people) but doesn't scale to large meetings. Rooms are capped at 6 people for that reason. If you outgrow this, the natural next step (from the original roadmap) is a real SFU media server like **mediasoup**, **Janus**, or **LiveKit**, which routes media through a single server instead of everyone connecting to everyone.

### About screen sharing

Screen sharing works by swapping the video track your camera was sending for a track from your screen (`getDisplayMedia`), without needing to reconnect — so it should feel instant. A few practical notes:
- Works reliably on desktop Chrome, Firefox, and Edge. Support on mobile browsers is inconsistent (some recent Android/Chrome versions support it; iOS Safari's support is limited), so treat this as primarily a desktop feature for now.
- Only your video is swapped — audio always comes from your microphone, not your screen/system audio.
- If you also have your camera "hidden," turning that back on while screen sharing won't do anything visible until you stop sharing, since the screen feed takes priority on the video track.

### About the saved signaling server URL

Like Google Meet, you shouldn't have to think about infrastructure every time you join a call — so the signaling server URL is saved to your Supabase account (as user metadata) the first time you enter it, and pre-filled automatically every time you log in afterwards, on any device. Editing the field and clicking elsewhere re-saves it. The only exception: opening someone else's invite link overrides it for that session, since the link points at whichever server *they're* using — your own saved default comes back the next time you visit without a link.

### About file sharing

Files travel over the same peer-to-peer DataChannel as chat text — never through the signaling server. A few practical details:

- **25MB limit per file.** DataChannels aren't designed for large transfers, and files are sent in small chunks with flow control (to avoid overwhelming the connection), so anything much bigger would be slow and fragile. For genuinely large files, a traditional file-sharing link is still the better tool.
- **Sent to everyone currently in the call**, the same as a chat message — there's no way to send a file to just one person in a group call yet.
- **Images get a thumbnail preview** inline in the chat bubble; everything else shows a file icon, name, and size, with a download link that appears once the transfer completes.
- **The file itself never touches the signaling server** — same DTLS-encrypted guarantee as video and chat.
- Progress is genuinely live: the sender shows "Sending… X%" and the receiver shows "Receiving… X%" as chunks arrive, not just a spinner.
- If you leave the call mid-transfer, the transfer simply stops — there's no resume.

### About incoming-call notifications

Click **🔔 Enable call alerts** in the account bar (once per browser/device) and you'll get a real push notification whenever someone starts a call in a room you're invited to — even if your browser is completely closed. This works differently from everything else in this app: instead of your browser polling or staying connected, the browser vendor's own push infrastructure (Chrome's, Firefox's, etc.) delivers it and wakes a small background script to show the notification. Clicking the notification opens (or focuses) the app with that room pre-filled, ready to join.

A few things worth knowing:
- **Only fires when someone *starts* a call** — i.e. the first person joins an empty room. Reconnecting to an already-in-progress call doesn't re-notify everyone.
- **Only goes to people on that room's invite list** (or its owner), excluding whoever's starting the call.
- **Per-device, not per-account.** Enabling it on your laptop doesn't enable it on your phone — each browser/device you want alerts on needs its own click of the button.
- **Brave users need one extra one-time step.** Brave disables Google's push messaging service by default for privacy, which breaks Web Push (the standard this feature relies on) until it's turned back on: go to `brave://settings/privacy` → enable **"Use Google services for push messaging"** → reload the page. Chrome, Firefox, Edge, and desktop Safari don't need this — it's specific to Brave's defaults. There's no way for this app to detect "you're on Brave, here's a prompt" automatically (browsers don't expose that to websites), so this is just worth knowing about or mentioning to anyone you invite who uses Brave.
- **If the "Enable call alerts" button shows "blocked" on Android Chrome, check the Android system setting first, not just Chrome's own site permissions.** The on-page diagnostic line under the account bar (see below) will confirm the browser thinks notifications are `denied`. If Chrome's own site-level permission looks fine (e.g. "Ask every time," not explicitly blocked) and resetting it doesn't help, the actual block is often at the OS level: Android **Settings → Apps → Chrome → Notifications**, which can be toggled off independently of anything inside Chrome, and silently overrides it. This is easy to miss since Chrome's in-app settings don't visibly reflect that the OS is the one blocking it.
- If your browser blocks notifications (denied permission), the button shows "🔕 Call alerts blocked" — you'd need to re-allow notifications for the site in your browser's own settings to fix that, this app can't override a browser-level block.
- This is scoped to just "someone's calling" alerts. Missed-call history and new-message notifications (also mentioned in the original roadmap) aren't built yet — those would be natural additions on top of this same push infrastructure.
- **Troubleshooting without devtools:** right under the account bar, a small diagnostic line reports the concrete facts — whether the service worker registered (and its exact error if not), whether the Push and Notification APIs exist, and the current permission state. This is visible on any device, phone included, without needing remote debugging or a connected computer — genuinely useful the moment "Enable call alerts" doesn't behave as expected.

#### One-time setup: generate your push notification keys

This uses the **Web Push** standard, which requires a VAPID key pair (a way for your backend to prove to browsers' push services that notifications are really coming from you, not someone else).

1. Locally, run:
   ```bash
   pip install cryptography
   python backend/generate_vapid_keys.py
   ```
2. It prints two values. In Render, add them as two more environment variables:
   - `VAPID_PRIVATE_KEY_PEM` → the whole PEM block it printed, including the `BEGIN`/`END` lines (multi-line env vars are fine on Render)
   - `VAPID_PUBLIC_KEY` → the single line it printed
3. Also add `VAPID_CONTACT_EMAIL` → any contact email (e.g. your own). This is required by the push spec as a way for browser vendors to reach you about your usage of their push service if needed — it doesn't need to be actively monitored.

No changes to `frontend/index.html` are needed for this — the frontend fetches the public key from your backend automatically (`/vapid-public-key`), the same way it already fetches nothing else hardcoded like this. If you skip this setup, the rest of the app works exactly as before; the notification button just won't do anything (the backend silently no-ops instead of erroring).

### About switching between voice and video mid-call

You can go from camera-on to audio-only and back at any point during a call — no hanging up or reconnecting required. The **camera button** (labeled "Turn off camera" / "Turn on camera") does this: turning the camera off releases the device entirely (the camera light turns off) and your tile switches to an avatar with your initial; turning it back on requests the camera fresh and switches back to video, for everyone in the call, live.

Checking "Join audio only" before clicking Join call just sets your *starting* state — it's no longer a permanent choice for that call the way it was in the previous version. You can turn your camera on later, or start with video and turn it off later, freely.

Screen sharing also works regardless of whether your camera is currently on — sharing your screen while you're audio-only, or while your camera is on, both work the same way; stopping the share returns you to whichever state (camera on, or audio-only) you were in before.

Under the hood, this works without reconnecting because every participant's connection reserves a "video slot" from the moment they join, whether or not anything is actually being sent through it yet — turning the camera on, off, or switching to a screen share just swaps what's flowing through that already-open slot. That's also why nobody sees a jarring drop or a "reconnecting" message when you toggle: the underlying connection never actually changes, just its contents.

### About invite links

An invite link looks like:
```
https://your-app.vercel.app/?server=https%3A%2F%2Flivecam-signaling.onrender.com&room=amber-falcon-71
```
It just pre-fills the two setup fields — it does **not** auto-join the call and does **not** log anyone in. That's intentional: whoever opens the link still has to sign in and click Join call themselves, so nothing about their camera or microphone happens without a deliberate action on their end. Anyone with a valid account and the link can join the room, so treat it like a room key: share it only with the people you're calling, and generate a fresh room code for each call if you want old links to stop working.

### About real accounts and invite-only rooms

Joining requires a real Supabase account (email + password), and each room is restricted to specific invited emails on top of that. A few things worth knowing:

- **The server never sees passwords.** Supabase handles sign-up, login, and password storage entirely; your browser only ever holds a short-lived **session token**, which the signaling server checks by asking Supabase's own API "is this token valid?" — it never touches or stores the password itself.
- **Whoever connects to a room code first becomes its owner.** Their email is automatically allowed. Nobody else can join that room unless the owner explicitly adds their email.
- **The "Allow these emails in" field is how the owner invites people.** It's a comma-separated list, and it only has any effect when *you're* the room's owner — if you're joining someone else's room, leave it blank; entering emails there does nothing unless you own the room. Entries are additive: re-entering the field with more emails adds them to the existing list, it never removes anyone.
- **To add someone after the room already exists**, the owner just reconnects (rejoin the call) with that person's email in the field — the list updates each time the owner connects with a non-empty field. There's currently no in-call "add someone now" button; you'd hang up, add the email, and rejoin.
- Anyone who isn't the owner and isn't on the list gets turned away with a clear "you haven't been invited" message before they're let into the room.
- This lives in a small Supabase table (`room_access`) reached only through the **service_role key**, which the frontend never sees — only the backend, via a Render environment variable. That's what actually enforces the restriction; the frontend field is just how the owner tells the backend who to add.
- Still not "enterprise-grade": there's no UI for an owner to *remove* someone once added, no room deletion, and Supabase's free tier has its own limits (e.g. monthly active user caps) worth checking if this gets real usage.

#### One-time setup: point the app at your Supabase project

You already created a Supabase project. From **Project Settings → API**, you'll need three values total now:

1. Open `frontend/index.html`, find this near the top of the `<script>` block:
   ```js
   const SUPABASE_URL = 'https://YOUR-PROJECT.supabase.co';
   const SUPABASE_ANON_KEY = 'YOUR-ANON-PUBLIC-KEY';
   ```
   Replace both with your actual **Project URL** and **anon public** key. (The anon key is designed to be public/embedded in frontend code — it's not a secret.)

2. In **Render**, open your backend service → **Environment**, and add three environment variables:
   - `SUPABASE_URL` → your Project URL
   - `SUPABASE_ANON_KEY` → your anon public key
   - `SUPABASE_SERVICE_ROLE_KEY` → your **service_role** secret key (also from Project Settings → API, listed below the anon key)

   ⚠️ The service_role key bypasses all of Supabase's security rules. It must **only** ever live in Render's environment variables — never in `frontend/index.html`, never committed to the repo, never sent to a browser. Render's environment variables aren't visible in your public GitHub repo, which is why this split exists.

3. In Supabase, go to **SQL Editor → New query**, paste in the contents of `backend/supabase_setup.sql`, and run it once. This creates the `room_access` and `push_subscriptions` tables the backend needs. You won't need to touch them again — the backend manages rows in them automatically.

(`render.yaml` already declares all six environment variables as required — Render will prompt you to fill them in if you deploy fresh, or you can add them anytime under the service's Environment tab and it'll redeploy.)

## 1. Deploy the backend (Render)

1. Push this repo to GitHub.
2. In [Render](https://render.com), click **New → Web Service**, connect the repo, and point it at the `backend/` folder.
3. Render should auto-detect `render.yaml`. If not, set manually:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add the six required environment variables under the service's **Environment** tab: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `VAPID_PRIVATE_KEY_PEM`, `VAPID_PUBLIC_KEY`, `VAPID_CONTACT_EMAIL` (see "One-time setup" sections above).
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
3. In "Allow these emails in," list the email addresses of everyone you want to be able to join — this makes you the room's owner. Your own email is always included automatically.
4. Click **Copy invite link**, and send that link to everyone you invited.
5. Each person opens the link — server and room fields are pre-filled — logs in or signs up with the **same email you invited**, leaves "Allow these emails in" blank, and clicks **Join call**, allowing camera/mic access when prompted.

Within a few seconds everyone should see and hear each other in the grid (labeled by email), and you can open the chat panel or share your screen from there. If you need to invite someone new mid-project, hang up, add their email to the field, and rejoin — you'll still be recognized as the owner since you were first.

## Notes on reliability & security

- **NAT traversal:** a public STUN server and a free public TURN test server (openrelay.metered.ca) are included so this works across different WiFi/cellular networks. The public TURN server is rate-limited and fine for personal testing, but swap in your own (e.g. a paid Twilio or Cloudflare TURN service) if you rely on this daily.
- **Access control is a real Supabase account plus being on the specific room's invite list.** Only the room's owner and the emails they've explicitly added can join — this is checked server-side against the `room_access` table before anyone's even let into the signaling room. Keep room codes and links private regardless, since the owner is whoever happens to connect first with a given code.
- **The `service_role` key is the most sensitive credential in this whole project.** It bypasses every one of Supabase's security rules. It belongs only in Render's environment variables — never in `frontend/index.html`, never committed to Git. If it ever leaks, rotate it immediately from Supabase's dashboard (Project Settings → API → regenerate).
- **Video, audio, and chat are all encrypted in transit** — WebRTC requires DTLS/SRTP for media and DataChannels by design, so this is on by default, not something extra that was bolted on. The signaling server never sees any of that content, only the connection setup messages needed to establish it, plus each user's email (for labeling tiles/chat) and a token it forwards to Supabase to verify.
- **HTTPS/WSS only in production** — browsers block camera access and mixed-content WebSocket connections over plain HTTP, so make sure you're using the `https://`/`wss://` URLs Render and Vercel give you.
- **The Supabase anon key is meant to be public** and is safe to leave in the frontend file — it identifies your project, it doesn't grant special access on its own. Don't confuse it with the `service_role` key (which you never need for this app, and should never expose in frontend code).
- **Push subscription details are stored server-side too** (in `push_subscriptions`, reached only via the service_role key, same protection as `room_access`). A push subscription isn't a secret exactly, but it is a capability — anyone holding it could send that browser a push through your VAPID identity — so it's treated with the same "service_role key only" access pattern as everything else sensitive in this project.

## Local testing (optional, before deploying)

Backend (needs all six environment variables set locally too):
```bash
cd backend
pip install -r requirements.txt
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_ANON_KEY=your-anon-key
export SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
export VAPID_PRIVATE_KEY_PEM="$(cat path/to/private_key.pem)"
export VAPID_PUBLIC_KEY=your-vapid-public-key
export VAPID_CONTACT_EMAIL=you@example.com
uvicorn main:app --reload --port 8000
```
Then use `ws://localhost:8000` as the signaling server URL, and open `frontend/index.html` directly in two browser tabs (camera access works on `localhost` without HTTPS; make sure you've already edited the `SUPABASE_URL`/`SUPABASE_ANON_KEY` constants in that file too). Note: testing across two different devices locally requires them to reach your computer's local IP and both being HTTPS or on the same trusted network, which is why real testing is easiest once deployed.

One exception: **push notifications won't work by just opening `index.html` as a local file** (`file://`) — service workers require a real `http://` or `https://` origin. If you want to test notifications before deploying, serve the frontend folder with a simple local server (e.g. `npx serve frontend`) instead of opening the file directly.

## What's next (from the original roadmap)

This covers **Phase 1 (one-way viewer)**, **Phase 2 (two-way calls)**, **Phase 3 (chat + invite links)**, **Phase 4 (voice/video, switchable live mid-call)**, **group calls**, **screen sharing**, **real accounts** (Supabase auth), **invite-only rooms** (per-room allow-lists), a **saved signaling server URL** per account, **file sharing** in chat, and **incoming-call push notifications**. Natural next steps from your original plan: missed-call history and new-message notifications (built on the same push infrastructure), recording, an in-call UI to add/remove invited people without hanging up, a real SFU media server for larger meetings, emoji reactions and voice messages, and eventually a full encrypted messenger. Happy to help scope any of those next.
