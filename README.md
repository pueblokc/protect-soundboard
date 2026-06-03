<div align="center">

```
   ___          _            _     ____                       _ _                      _
  | _ \_ _ ___ | |_ ___  __ | |_  / ___| ___  _   _ _ __   __| | |__   ___   __ _ _ __| |
  |  _/ '_/ _ \|  _/ -_)/ _||  _| \___ \/ _ \| | | | '_ \ / _` | '_ \ / _ \ / _` | '__| |
  |_| |_| \___/ \__\___|\__| \__| |____/\___/ \__,_|_| |_|\__,_|_.__/ \___/ \__,_|_|  |_|
                                                                                          
        📣  Talk, play sounds, and trigger the siren on a UniFi Protect AI Horn  📢
```

# Protect Soundboard

**Tap-to-play messages, sound effects, and siren control for the UniFi Protect AI Horn Speaker + PoE Siren.**

A small, self-hosted, mobile-first PWA. Type any text and your AI Horn *speaks it* in a premium voice.
Tap a preset to bark "get off my lawn." Slam the siren. All from your phone.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Flask](https://img.shields.io/badge/flask-3.x-black)
![Verified on Protect 7.x](https://img.shields.io/badge/UniFi%20Protect-7.x-green)

<sub>An open tool by <b><a href="https://kccsonline.com">KCCS</a></b> · © 2026 · kccsonline.com</sub>

</div>

---

## 📸 Screenshots

<div align="center">

| Mobile | Desktop |
|:---:|:---:|
| ![Soundboard mobile UI](assets/screenshots/mobile.png) | ![Soundboard desktop UI](assets/screenshots/desktop.png) |

</div>

---

## ✨ What it does

| Feature | Description |
|---------|-------------|
| 🗣️ **Say something** | Type any text → the AI Horn speaks it aloud, live. |
| 💬 **Message presets** | One-tap spoken messages — *Get Off My Lawn*, *Intruder Alert*, *You're Being Recorded*, *No Soliciting*… (fully editable in-app). |
| 🔊 **Sound effects** | Drop any `.mp3`/`.wav` into `sounds/` and it appears as a button. Air horn, alarm beep, dog bark, whatever. |
| 🚨 **Siren** | Trigger the PoE siren for 5 / 10 / 30 / 60 s, or stop it instantly. |
| 🎚️ **Volume** | Live sliders for horn + siren, clamped to a safety cap (default 60%), with an **Allow loud** override. |
| 🟢 **Live status** | Connection LEDs + current volume for both devices, refreshed every 8 s. |
| 📱 **Installable PWA** | Add to home screen; runs full-screen like a native app. |

---

## 🧠 How it works (the interesting part)

There are **three** distinct control paths, and which one fires depends on what you're doing:

```
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                          Protect Soundboard (Flask)                      │
   └─────────────────────────────────────────────────────────────────────────┘
        │                          │                              │
        │ arbitrary audio          │ free built-in TTS            │ siren + volume
        │ (files + ElevenLabs TTS) │ (fallback voice)             │
        ▼                          ▼                              ▼
  ╔══════════════════╗     ╔══════════════════════╗     ╔═══════════════════════╗
  ║  TALKBACK (WS)   ║     ║  POST /automations/  ║     ║  PATCH /sirens/{id}    ║
  ║  stream AAC-LC   ║     ║       run            ║     ║  PATCH /speakers/{id}  ║
  ║  frames to horn  ║     ║  (Test-Alarm dry-run)║     ║  (direct private API)  ║
  ╚══════════════════╝     ╚══════════════════════╝     ╚═══════════════════════╝
        │                          │                              │
        ▼                          ▼                              ▼
   AI Horn plays            AI Horn speaks                Siren sounds /
   any audio you want       built-in voice                volume changes
```

1. **Talkback WebSocket stream** — the workhorse. Open `wss://<nvr>/proxy/protect/ws/talkback?speaker=<id>`,
   then push **frame-aligned AAC-LC ADTS** (24 kHz mono) as binary WS frames, paced ~42.7 ms each. The NVR
   relays each frame to the speaker as UDP. This streams **arbitrary audio** — any file, or the MP3 bytes from
   a premium TTS engine — with **no automations, no owner account, and no 2FA dance**.
2. **Built-in TTS fallback** — `POST /proxy/protect/api/automations/run` with a `PLAY_TEXT_ON_SPEAKER` action
   runs the console's *Test Alarm* dry-run, which speaks text live in UniFi's free built-in voice. Used
   automatically when no ElevenLabs key is configured.
3. **Direct private-API PATCH** — siren on/off (`sirenStatus`) and volume are simple authenticated `PATCH`
   calls against `/sirens/{id}` and `/speakers/{id}`.

> 📖 **Full technical write-up with copy-paste code:** [`docs/CONTROLLING-UNIFI-PROTECT-AUDIO.md`](docs/CONTROLLING-UNIFI-PROTECT-AUDIO.md)
> — how to authenticate, discover your device IDs, stream audio, speak TTS, trigger the siren, and set volume,
> independent of this app.

---

## 🚀 Quick start

```bash
git clone https://github.com/pueblokc/protect-soundboard
cd protect-soundboard

cp .env.example .env          # fill in your NVR host + a Protect local admin
pip install -r requirements.txt
#  ↑ also requires ffmpeg on PATH  (winget install ffmpeg | brew install ffmpeg | apt install ffmpeg)

python app.py                 # → http://127.0.0.1:5123
```

Then open `http://127.0.0.1:5123` on your phone or desktop. Done.

### Configure your devices

Edit `config.json` and replace the placeholders with **your** speaker/siren IDs. To find them, hit your NVR's
bootstrap endpoint once:

```bash
# Log in, grab the device IDs from the speakers[] and sirens[] arrays:
curl -sk https://<NVR>/proxy/protect/api/bootstrap -b cookies.txt | python -m json.tool | grep -A3 '"speakers"\|"sirens"'
```

(The doc above walks through this step in detail.)

| Key | What it is |
|-----|-----------|
| `host` | Your NVR / console IP or hostname |
| `devices.horn_id` | The speaker's `id` from `bootstrap.speakers[]` |
| `devices.horn_mac` | That speaker's `mac` (used by the built-in-TTS fallback) |
| `devices.siren_id` | The siren's `id` from `bootstrap.sirens[]` |
| `max_volume` | Safety cap (0–100). The UI's "Allow loud" toggle overrides it per-press. |
| `tts_mode` | `elevenlabs_then_unifi` (premium, fall back to free), `elevenlabs`, or `unifi` |

---

## 🧩 Requirements

- **Python 3.9+**
- **ffmpeg** on `PATH` — talkback transcodes everything to AAC-LC through it. *No ffmpeg = no audio* (silent fail).
- A **UniFi Protect** console on **7.x** with an **AI Horn Speaker** (or any Protect speaker) and, optionally, a **PoE siren**.
- A **Protect local admin account** (created in console OS Settings → Admins). Use a dedicated minimal one.
- *(Optional)* an **ElevenLabs** API key for premium TTS. Without it, the app uses UniFi's free built-in voice.

---

## 🔐 Security notes

- **No secrets live in this repo.** Credentials come from `.env` (git-ignored) or your service manager's
  environment. `config.json` holds only device IDs and tuning — no passwords.
- Use a **dedicated, minimal-permission Protect local account** for the app rather than your primary login.
- This app talks to Protect's **private/undocumented API**. It works today on Protect 7.x; Ubiquiti can change
  it. Treat it as best-effort, not a supported integration.
- Don't expose the app to the public internet. Keep it on your LAN or a private overlay (Tailscale/WireGuard),
  behind a reverse proxy that restricts source IPs.

---

## 📂 Project layout

```
protect-soundboard/
├── app.py                # Flask app + JSON API
├── protect_engine.py     # private-API: status, siren, volume, built-in TTS
├── talkback.py           # WebSocket audio streaming (the real magic)
├── tts.py                # ElevenLabs → mp3 bytes, disk-cached
├── config.json           # device IDs + tuning (NO secrets)
├── presets.json          # editable spoken-message presets
├── sounds/               # drop audio files here → they become buttons
├── templates/index.html  # the whole mobile-first UI (single file)
├── static/               # PWA manifest, service worker, icon
└── docs/
    └── CONTROLLING-UNIFI-PROTECT-AUDIO.md   # standalone how-to + code
```

---

## ⚠️ Disclaimer

Not affiliated with or endorsed by Ubiquiti. "UniFi" and "Protect" are trademarks of Ubiquiti Inc.
This tool uses undocumented endpoints at your own risk. Be a good neighbor — don't blast your siren at 3 a.m.

---

<div align="center">

```
        ┌─────────────────────────────────────────────┐
        │   ◣◢   K C C S   ◣◢    ·    kccsonline.com   │
        │   built for people who run their own gear    │
        └─────────────────────────────────────────────┘
```

**© 2026 KCCS · [kccsonline.com](https://kccsonline.com)** · MIT Licensed

</div>
