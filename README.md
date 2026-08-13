# LiveCam mobile (Capacitor)

Wraps the exact same `frontend/index.html` used on the website in a native
iOS/Android shell, for app store distribution. There's deliberately only
one copy of the frontend — `npm run sync` copies it in from `../frontend`
before every Capacitor sync, so there's nothing to keep hand-in-sync.

## What's already done

- **Capacitor project scaffolded** — `android/` and `ios/` native projects
  exist and build against the current frontend.
- **Camera/mic permissions declared** on both platforms (`AndroidManifest.xml`,
  `Info.plist`) — without these, `getUserMedia()` (used for calls) fails
  silently on Android and gets your app rejected in iOS review.
- **Push notification permission declared** on both platforms.
- **Frontend bootstrap added** (`index.html`, guarded by
  `window.Capacitor.isNativePlatform()` so it's a complete no-op on the
  website):
  - Status bar styled to match the app's dark theme.
  - Native push registration — requests permission, registers for a device
    token, and sends it to the backend via the **existing**
    `/push/subscribe-device` endpoint (same auth pattern as the web push
    flow: a Supabase session token, not a new auth mechanism).
- **Backend push delivery already exists** — `main.py` already sends
  through Firebase Cloud Messaging to any registered device token
  wherever it sends a web push (incoming call, new message, scheduled call
  reminder). This was built before this round of work; nothing here
  duplicates it.

## What's left — and why it has to happen on your machine

I can't finish this from here: building and signing an iOS app requires
Xcode on macOS, and both stores require credentials tied to *your* Apple/
Google developer accounts, which only you can create.

### 1. Firebase project (needed for both platforms' push)

The backend already knows how to send through Firebase — it just needs a
project to send *through*.

1. Create a project at [console.firebase.google.com](https://console.firebase.google.com).
2. Add an Android app (package name: `com.prolaw.livecam`, matching
   `capacitor.config.json` — change both together if you want a different
   id). Download `google-services.json`, place it at
   `android/app/google-services.json`.
3. Add an iOS app (bundle id: `com.prolaw.livecam`). Download
   `GoogleService-Info.plist`, place it at
   `ios/App/App/GoogleService-Info.plist`.
4. In Firebase project settings → Cloud Messaging, upload your **APNs Auth
   Key** (a `.p8` file from your Apple Developer account, Certificates →
   Keys) — this is what lets Firebase actually deliver to iOS devices
   under the hood.
5. Project settings → Service accounts → **Generate new private key**.
   Set the entire downloaded JSON as the `FIREBASE_SERVICE_ACCOUNT_JSON`
   environment variable in Render. That's the only backend change needed —
   the code that uses it is already deployed.

### 2. Android build (Android Studio, any OS)

```
cd mobile
npm install
npm run sync
npm run open:android
```

Opens in Android Studio. You'll need to set up a signing keystore for a
release build (Build → Generate Signed Bundle/APK) before uploading to
Play Console.

### 3. iOS build (Xcode, macOS only)

```
cd mobile
npm install
npm run sync
npm run open:ios
```

Opens in Xcode. Set your Team under Signing & Capabilities (needs an
Apple Developer Program membership, $99/yr), add the **Push Notifications**
capability there too, then Product → Archive to build for TestFlight/App
Store submission.

### 4. App icon & splash screen

Both platforms currently use Capacitor's placeholder icon. Put a
1024×1024 source icon at `resources/icon.png` and a splash image at
`resources/splash.png`, then run:

```
npx @capacitor/assets generate
```

This generates every required size for both platforms automatically.

### 5. Test on a real device before submitting

WebRTC camera/mic behavior inside a native WebView can differ subtly from
a desktop/mobile browser — worth a real end-to-end call test (not just the
simulator/emulator, which often can't access a real camera) before
submitting to either store.

### 6. Store submission

- **Apple**: Developer Program membership, App Store Connect listing,
  privacy policy URL (required — this app handles camera/mic/contacts-like
  data), screenshots, review can take 1–3 days.
- **Google**: Play Console account ($25 one-time), Data Safety form (same
  privacy reasons), screenshots, review is usually faster than Apple's.

## Day-to-day workflow once set up

Every time you change `frontend/index.html`:

```
cd mobile
npm run sync
```

This copies the latest frontend in and re-syncs both native projects. Then
rebuild from Android Studio / Xcode as usual.
