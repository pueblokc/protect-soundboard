# Controlling a UniFi Protect AI Horn — Siren, Volume, and Text-to-Speech

```
   ▟▛  How to make a UniFi Protect speaker talk, play audio, and sound its siren  ▜▙
   ▘                              from your own code                              ▝
```

A practical, code-first guide to driving a **UniFi Protect AI Horn Speaker** (and PoE siren) over
Protect's private API. Everything here is **verified on Protect 7.x** and is exactly what the
[Protect Soundboard](../README.md) app does under the hood.

> ⚠️ **These are undocumented/private endpoints.** They work today; Ubiquiti can change them in any release.
> Nothing here is an officially supported integration. Use a dedicated, minimal-permission local account and
> keep it on your own network.

> 🎯 **TL;DR — which method gives clean audio?**
> For **speech, use native TTS** (§5 — `PLAY_TEXT_ON_SPEAKER`). It's robotic but **reliable and never garbles**.
> The **talkback stream** (§4) is the *only* way to play an arbitrary audio **file** on the horn, but on
> **Protect 7.1.x it garbles intermittently** (host-independent — reproduced from a clean, idle machine), so
> treat it as best-effort. See [§7 — garble & two leads toward a clean file path](#7-the-talkback-garble-and-two-leads-toward-a-clean-file-path).

There are **three** independent control paths. Pick the one that matches what you want:

| Goal | Mechanism | Quality | Section |
|------|-----------|---------|---------|
| Speak **text** (the reliable path) | `POST /automations/run` → `PLAY_TEXT_ON_SPEAKER` | ✅ clean, robotic | [§5](#5-built-in-text-to-speech-free-and-clean) |
| Play an **arbitrary audio file** | **Talkback WebSocket stream** | ⚠️ best-effort, may garble on 7.1.x | [§4](#4-stream-arbitrary-audio-talkback) |
| **Siren** on/off and **volume** | Direct `PATCH` on the device | ✅ clean | [§3](#3-siren-and-volume-direct-patch) |

---

## 1. Authenticate

Protect uses a cookie/CSRF session. Log in once, reuse the session.

```python
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = "192.168.1.100"        # your NVR / console
USER = "soundboard"           # a Protect LOCAL admin (OS Settings -> Admins)
PASS = "********"

s = requests.Session()
s.verify = False              # self-signed cert on the console
r = s.post(f"https://{HOST}/api/auth/login",
           json={"username": USER, "password": PASS}, timeout=15)
r.raise_for_status()

# The CSRF token is needed for write (PATCH/POST) calls:
csrf = r.headers.get("X-Updated-Csrf-Token") or r.headers.get("X-CSRF-Token")
if csrf:
    s.headers["X-CSRF-Token"] = csrf

# A login cookie named TOKEN is also set — you'll need it for the WebSocket later.
token = s.cookies.get("TOKEN")
```

The Protect REST API lives under `https://<host>/proxy/protect/api`.

---

## 2. Discover your device IDs

Everything keys off the device `id`. Pull them from the bootstrap document:

```python
api = f"https://{HOST}/proxy/protect/api"
boot = s.get(f"{api}/bootstrap", timeout=20).json()

for spk in boot.get("speakers", []):
    print("SPEAKER", spk["id"], spk.get("mac"), spk.get("name"), spk.get("volume"))
for sir in boot.get("sirens", []):
    print("SIREN  ", sir["id"], sir.get("name"), sir.get("volume"))
```

Note three things for later:
- the speaker **`id`** (for talkback + volume),
- the speaker **`mac`** (for the built-in-TTS fallback),
- the siren **`id`** (some hardware exposes the siren as a separate device; on others the speaker *is* the siren).

---

## 3. Siren and volume (direct PATCH)

The simplest path. Authenticated `PATCH` calls, no streaming, no automations.

### Volume (0–100)

```python
# Speaker volume:
s.patch(f"{api}/speakers/{SPEAKER_ID}", json={"volume": 45})
# Siren volume:
s.patch(f"{api}/sirens/{SIREN_ID}",    json={"volume": 30})
```

A safety habit: clamp to a cap unless the user explicitly opts into "loud":

```python
def clamp(v, cap=60, allow_loud=False):
    v = max(0, min(100, int(v)))
    return v if allow_loud else min(v, cap)
```

### Siren on / off

`duration` is in **milliseconds**.

```python
# Sound the siren for 10 seconds:
s.patch(f"{api}/sirens/{SIREN_ID}",
        json={"sirenStatus": {"isActive": True, "duration": 10_000}})

# Stop it early:
s.patch(f"{api}/sirens/{SIREN_ID}",
        json={"sirenStatus": {"isActive": False}})
```

Read current state back from bootstrap: `siren["sirenStatus"]["isActive"]`.

---

## 4. Stream arbitrary audio (talkback)

Protect's **talkback** channel — the same path the mobile app uses for push-to-talk — accepts a live audio
stream that the NVR relays to the speaker. Feed it the bytes of *any* audio file (or the MP3 a TTS engine
hands you) and the horn plays it. **No automation, no owner account, no frozen/preset content.** This is the
**only** way to play an arbitrary audio *file* on the horn.

> ⚠️ **It can garble on Protect 7.1.x.** In practice this path is intermittently choppy/garbled on current
> firmware, and it's **host-independent** (reproduced from a clean, idle machine, so it isn't your CPU/GC/timer
> jitter). For **speech, prefer native TTS (§5)** — it never garbles. Use talkback for non-critical sound
> effects, and see [§7](#7-the-talkback-garble-and-two-leads-toward-a-clean-file-path) for two leads toward a
> garble-free file path.

### The shape of it

```
  your code ──ffmpeg──▶ AAC-LC ADTS frames (24kHz mono)
        │
        │  one frame per WebSocket binary message, paced ~42.7 ms apart
        ▼
  wss://<nvr>/proxy/protect/ws/talkback?speaker=<id>
        │
        ▼  NVR relays each frame as UDP to the speaker
   🔊 AI Horn
```

Two things make or break it:

1. **Codec & framing.** The stream must be **AAC-LC in an ADTS container, 24 kHz, mono.** You split the
   ADTS bytestream into individual frames and send **one frame per binary WS message**.
2. **Pacing.** Each AAC frame is `1024 / 24000 ≈ 42.7 ms` of audio. Send them on that schedule. The speaker
   has a small (~0.5 s) jitter buffer — deliver a little *ahead* of real time to keep it full, but bursting
   the whole file at once overruns it and the audio garbles.

### Transcode with ffmpeg

```python
import subprocess

def to_adts(path, ffmpeg="ffmpeg"):
    p = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path,
         # add ~0.9s leading silence so the speaker's post-arm warmup doesn't clip the first word:
         "-af", "adelay=900:all=1",
         "-c:a", "aac", "-profile:a", "aac_low", "-ar", "24000", "-ac", "1",
         "-b:a", "32k", "-f", "adts", "pipe:1"],
        capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace")[:300])
    return p.stdout

def split_adts(buf):
    """Split an ADTS bytestream into individual frames."""
    out, i, n = [], 0, len(buf)
    while i + 7 <= n:
        if buf[i] != 0xFF or (buf[i+1] & 0xF0) != 0xF0:   # ADTS sync word
            i += 1; continue
        flen = ((buf[i+3] & 0x03) << 11) | (buf[i+4] << 3) | (buf[i+5] >> 5)
        if flen < 7 or i + flen > n:
            break
        out.append(buf[i:i+flen]); i += flen
    return out
```

### Open the socket and pace the frames

```python
import ssl, time, websocket   # pip install websocket-client

FRAME_DT = 1024 / 24000.0     # ~0.0427 s of audio per AAC frame
LEAD     = 0.40               # seconds to run ahead of real time (keeps the buffer full)

def stream(host, token, speaker_id, file_path, ffmpeg="ffmpeg"):
    frames = split_adts(to_adts(file_path, ffmpeg))
    if not frames:
        raise RuntimeError("no audio frames produced")

    ws = websocket.create_connection(
        f"wss://{host}/proxy/protect/ws/talkback?speaker={speaker_id}",
        header=[f"Cookie: TOKEN={token}"],
        sslopt={"cert_reqs": ssl.CERT_NONE},
        origin=f"https://{host}", timeout=15)

    time.sleep(0.4)            # let the device arm (startSinkStream)
    t0 = time.perf_counter()
    for n, fr in enumerate(frames):
        target = t0 + n * FRAME_DT - LEAD
        while time.perf_counter() < target:
            pass                # tight wait → steady spacing
        ws.send_binary(fr)
    time.sleep(0.3)            # let the tail drain
    ws.close()
    return len(frames)
```

> 💡 **Why the busy-wait?** On Windows the default sleep granularity is ~15.6 ms, which is coarser than a
> 42.7 ms frame and makes pacing jittery. The production app in this repo additionally raises the timer
> resolution (`winmm.timeBeginPeriod(1)`), boosts process priority, and disables GC for the duration of the
> stream so nothing introduces a late frame. See [`talkback.py`](../talkback.py) for the hardened version.

Now you can play **anything**:

```python
stream(HOST, token, SPEAKER_ID, "air_horn.mp3")          # a sound effect
stream(HOST, token, SPEAKER_ID, elevenlabs_mp3_path)     # premium TTS (see §6)
```

---

## 5. Built-in text-to-speech (free and clean) ⭐ recommended for speech

Protect's own voice can speak text live, and **this is the path that never garbles** — it's the reliable way
to get speech onto the horn. It's the console's **"Test Alarm"** dry-run: `POST /automations/run` with a
`PLAY_TEXT_ON_SPEAKER` action. Plays immediately, saves nothing. The voice is robotic, but it's clean and
unlimited. (The Protect Soundboard app defaults to exactly this.)

```python
import uuid

def speak_builtin(text, speaker_mac):
    body = {
        "name": "_soundboard", "enable": True, "sources": [],
        "conditions": [{"condition": {"type": "is", "source": "webhook",
                                       "value": str(uuid.uuid4())}}],
        "historyConditions": [], "schedules": [],
        "actions": [{"type": "PLAY_TEXT_ON_SPEAKER", "order": -1, "metadata": {
            "text": text, "tone": "welcome", "type": "custom",
            "sources": [{"type": "include", "device": speaker_mac}]}}],
        "cooldown": {"enable": False, "timeout": 0},
    }
    r = s.post(f"{api}/automations/run", json=body, timeout=25)
    return r.status_code == 200

speak_builtin("Attention. You are on private property. Please leave.", SPEAKER_MAC)
```

Free and unlimited, but a plainer voice than a dedicated TTS engine.

---

## 6. Premium text-to-speech (talkback + a TTS API) — opt-in

For a natural voice, synthesize MP3 with any TTS provider and stream it via §4. **Because it rides the
talkback path, it inherits the 7.1.x garble** — so it's a nice-to-have, not the reliable path. Keep it
opt-in and fall back to §5 if you need guaranteed-clean speech. Example with ElevenLabs:

```python
import requests

def synth_elevenlabs(text, api_key, voice="EXAVITQu4vr4xnSDxMaL", model="eleven_turbo_v2_5"):
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        headers={"xi-api-key": api_key, "accept": "audio/mpeg",
                 "content-type": "application/json"},
        json={"text": text, "model_id": model,
              "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}},
        timeout=30)
    r.raise_for_status()
    return r.content          # mp3 bytes

mp3 = synth_elevenlabs("Smile, you're on camera.", MY_KEY)
# write mp3 to a temp file, then stream() it (talkback transcodes mp3 -> AAC via ffmpeg):
```

Cache by a hash of `(voice, model, text)` so repeated phrases don't re-bill the TTS API. The app does this in
[`tts.py`](../tts.py).

---

## 7. The talkback garble, and two leads toward a clean file path

On **Protect 7.1.x** the talkback stream (§4) is intermittently garbled. It is **host-independent**: the same
garble reproduces from a clean, idle machine, so it is *not* your CPU load, GC pauses, or timer granularity —
the usual pacing fixes (high-res timer, process priority) help but don't eliminate it. That's why, for speech,
**native TTS (§5) is the reliable choice** and this app defaults to it. For arbitrary sound-effect *files*,
talkback is currently the only option, so it's best-effort.

Two concrete leads (from the speaker's own `bootstrap` entry) that may yield a **garble-free file path** —
both unverified, offered as a starting point if you want to dig:

- **The horn's native talkback format is Opus, not AAC.** `bootstrap.speakers[].talkbackSettings` reports
  `{"typeFmt": "opus", "samplingRate": 24000, "bitsPerSample": 16, "channels": 1}`. This guide (and the app)
  send **AAC-LC ADTS**, which the NVR appears to accept and transcode — that transcode is a prime suspect for
  the garble. Encoding to Opus to match the device's native format (e.g. `ffmpeg -c:a libopus -ar 24000 -ac 1`,
  framed appropriately for the WS) is worth testing.
- **The horn stores audio clips on-device.** The same entry exposes `audioList` (a list of clip IDs) and
  `speakerState.files` (clips physically stored on the speaker, with sizes/MD5s, in ~10 MB of onboard storage).
  That implies a **native "play stored clip" action** exists — which, like native TTS, would play locally on
  the speaker and bypass streaming entirely (no garble). Uploading a clip and triggering it natively would be
  the clean way to play custom sound effects. Note `featureFlags.supportCustomRingtone` is `false` on this
  hardware, so whether *custom* clips are playable (vs. only built-in ones) needs verification.

If you crack either of these, please open an issue/PR — it would make custom audio on the horn as clean as TTS.

---

## 8. Gotchas & tips

| Symptom | Cause / fix |
|---------|-------------|
| **No audio at all** | `ffmpeg` not on `PATH`. Talkback silently produces nothing without it. |
| **First word clipped** | Speaker warm-up after arming. Add leading silence (`adelay=900` above). |
| **Garbled / choppy speech** | Use **native TTS (§5)** — talkback garbles on 7.1.x regardless of pacing (see §7). For *files*, keep the ~42.7 ms pacing and, on Windows, raise timer resolution / avoid GC mid-stream to minimize it. |
| **401 mid-session** | Session expired — re-login and retry the request once. |
| **WebSocket 4xx on connect** | Stale `TOKEN` cookie. Re-login to refresh it, then reconnect. |
| **`duration` ignored on siren** | It's **milliseconds**, not seconds. `10_000` = 10 s. |
| **Volume change "didn't take"** | You hit the speaker endpoint for a siren-only device (or vice-versa). Use the right `id`. |

---

## 9. Putting it together

A minimal end-to-end controller:

```python
login()                                    # §1  -> session + token
boot = bootstrap()                         # §2  -> SPEAKER_ID, SPEAKER_MAC, SIREN_ID

set_volume(SPEAKER_ID, 45)                 # §3
speak_builtin("Package delivered, thanks", SPEAKER_MAC)    # §5  clean native voice (preferred)
stream(HOST, token, SPEAKER_ID, "air_horn.mp3")            # §4  arbitrary file (best-effort, may garble)
# mp3 = synth_elevenlabs("Intruder alert", KEY); stream(...)  # §6  premium voice (opt-in, inherits garble)
siren(SIREN_ID, on=True, ms=10_000)        # §3  ten-second siren
```

That's the whole surface area. The [Protect Soundboard](../README.md) app wraps exactly these calls in a
mobile-first web UI with presets, a sounds folder, live status, and a volume safety cap.

---

<div align="center">

**© 2026 KCCS · [kccsonline.com](https://kccsonline.com)** · part of [protect-soundboard](../README.md) · MIT Licensed

</div>
