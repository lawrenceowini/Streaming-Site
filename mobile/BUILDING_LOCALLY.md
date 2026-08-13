# Building locally without Android Studio

This installs just the pieces actually needed to compile and install the
app — no IDE, and critically, no emulator (that's almost always what
kills a machine like a T460s; the SDK + command-line build itself is
fairly light).

You'll test on your actual phone over USB instead of an emulator. That's
not a downside here — for WebRTC camera/mic testing, a real device is
more trustworthy than an emulator anyway.

## 1. Install a JDK

Android's Gradle plugin needs JDK 17 or 21. If you don't already have one:

- **Windows/Mac/Linux**: install [Eclipse Temurin 21](https://adoptium.net/temurin/releases/) (pick the JDK, not JRE, installer for your OS).

Verify it worked:

```
java -version
```

## 2. Install just the Android command-line tools

Not the Android Studio installer — the **command-line tools only** package,
a few hundred MB instead of several GB:

<https://developer.android.com/studio#command-line-tools-only>

Android's `sdkmanager` expects a specific folder layout, so unzip it
carefully:

```
android-sdk/
  cmdline-tools/
    latest/          <- the unzipped contents go HERE, not directly in cmdline-tools/
      bin/
      lib/
      ...
```

Put `android-sdk/` wherever you like (e.g. `C:\android-sdk` on Windows, or
`~/android-sdk` on Mac/Linux).

## 3. Set environment variables

**Windows** (PowerShell, run once — restart your terminal after):

```powershell
[Environment]::SetEnvironmentVariable("ANDROID_HOME", "C:\android-sdk", "User")
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\android-sdk\cmdline-tools\latest\bin;C:\android-sdk\platform-tools", "User")
```

**Mac/Linux** (add to `~/.bashrc` or `~/.zshrc`):

```bash
export ANDROID_HOME="$HOME/android-sdk"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools"
```

Verify:

```
sdkmanager --version
```

## 4. Install the SDK packages Capacitor needs

```
sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"
sdkmanager --licenses
```

Accept every license prompt with `y`.

## 5. Build the APK

From the `mobile` folder:

```
npm install
npm run sync
cd android
```

Windows:

```
gradlew.bat assembleDebug
```

Mac/Linux:

```
chmod +x gradlew
./gradlew assembleDebug
```

First build downloads a bunch of Gradle dependencies, so it's slow (and
uses real CPU — expect fan noise on a T460s). After that, incremental
builds are much faster. The APK lands at:

```
android/app/build/outputs/apk/debug/app-debug.apk
```

## 6. Install it on your phone over USB — skip the emulator entirely

1. On your Android phone: **Settings → About phone → tap "Build number"
   seven times** to unlock Developer Options.
2. **Settings → Developer options → USB debugging → on.**
3. Plug the phone in via USB, allow the "Allow USB debugging?" prompt that
   appears on the phone.
4. From the `android` folder:

   ```
   adb install app/build/outputs/apk/debug/app-debug.apk
   ```

   (`adb` comes with `platform-tools`, already on your PATH from step 3.)

The app installs and you can open it directly — no emulator, no Android
Studio, and the whole loop (edit `frontend/index.html` → `npm run sync` →
`gradlew assembleDebug` → `adb install`) stays usable on modest hardware.

## If a build still feels too heavy

Cap how much memory Gradle uses so it doesn't compete with everything
else you have open — add to `android/gradle.properties`:

```
org.gradle.jvmargs=-Xmx2048m
org.gradle.daemon=false
```

`org.gradle.daemon=false` stops Gradle from keeping a background process
alive between builds — slightly slower per-build, but frees the RAM back
up immediately afterward instead of holding onto it.

If local builds are still rough even with this, that's exactly what the
GitHub Actions workflow is for — treat this as the option for quick local
iteration, and CI as the option for anything that needs to feel snappy.
