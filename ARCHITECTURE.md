# Architecture

Protect Soundboard is a small self-hosted Flask app plus a single-page, mobile-first PWA.
It drives a UniFi Protect AI Horn speaker and a PoE siren through Protect's **private,
undocumented** API on the local NVR/console. There is no database; state lives on the NVR
and in two JSON files.

## How it runs

```
phone / desktop browser ──HTTP──▶ Flask (app.py, default :5123)
                                    │
                                    ├─ protect_engine.py ──HTTPS (requests, self-signed cert)──▶ NVR /proxy/protect/api
                                    ├─ talkback.py ──ffmpeg──▶ AAC-LC ADTS ──WSS──▶ NVR /proxy/protect/ws/talkback ──UDP──▶ horn
                                    └─ tts.py ──HTTPS──▶ api.elevenlabs.io  (optional, opt-in)
```

* `python app.py` starts Flask's built-in threaded server on `0.0.0.0:<port>`.
* Configuration:
  * `config.json` — NVR host, port, volume cap, `tts_mode`, device IDs/MAC. No secrets.
  * Environment (or a git-ignored `.env`, parsed by `app.py` at import) — `PROTECT_NVR_HOST`,
    `PROTECT_NVR_USER`, `PROTECT_NVR_PASS`, `ELEVENLABS_*`, `FFMPEG`, `SOUNDBOARD_TOKEN`,
    `SOUNDBOARD_AUTH`.
  * `presets.json` — spoken-message buttons (label / icon / text / color).
  * `sounds/` — any audio file here becomes a button.
* Runtime files (git-ignored): `tts_cache/` (ElevenLabs mp3s keyed by hash),
  `.soundboard_token` (auto-generated access token, see *Auth* below).

## Modules

| File | Responsibility |
|------|----------------|
| `app.py` | Flask routes, `.env` loading, access-token auth + CSRF guard, input validation, lazy singletons for the engine and talkback client, single-stream lock. |
| `protect_engine.py` | `ProtectEngine`: cookie + CSRF session against `/api/auth/login`; `get_status` (bootstrap), `set_volume` (PATCH speaker/siren), `activate_siren` / `stop_siren` (PATCH `sirenStatus`), `speak_unifi` (POST `/automations/run` with `PLAY_TEXT_ON_SPEAKER` — the Test-Alarm dry-run). Re-logs in once on 401 and tracks rotated CSRF tokens. |
| `talkback.py` | `Talkback.stream()`: ffmpeg → 24 kHz mono AAC-LC ADTS, `split_adts` into frames, open `wss://…/ws/talkback?speaker=<id>` with the `TOKEN` cookie, send one frame per binary message on a 42.7 ms schedule (0.4 s lead). Windows-only timer/priority tweaks are no-ops elsewhere. |
| `tts.py` | ElevenLabs text→mp3, cached on disk by sha256(voice, model, text). Only used when `tts_mode` is not `unifi`. |
| `templates/index.html` | The whole UI (vanilla JS). Builds buttons from `/api/presets` and `/api/sounds`, polls `/api/status` every 8 s. |
| `static/` | PWA manifest, network-only service worker, icons. |

## HTTP API (all JSON, all under `/api`, all require the access token)

| Method | Path | Body | Effect |
|--------|------|------|--------|
| GET | `/api/status` | – | Horn/siren name, volume, connected, siren active, cap, TTS mode |
| GET | `/api/presets` | – | Contents of `presets.json` |
| POST | `/api/presets` | `{"tts":[{label,text,icon?,color?}]}` | Validated, atomically replaces `presets.json` |
| GET | `/api/sounds` | – | Audio file names in `sounds/` |
| POST | `/api/speak` | `{text, volume?, allow_loud?}` | Optional horn volume, then native TTS (or ElevenLabs→talkback when opted in) |
| POST | `/api/play-sound` | `{file, volume?, allow_loud?}` | Streams `sounds/<file>` via talkback (one stream at a time; 409 if busy) |
| POST | `/api/siren` | `{action:"activate"\|"stop", duration?, volume?, allow_loud?}` | Siren on (duration clamped 1–300 s) / off |
| POST | `/api/volume` | `{target:"horn"\|"siren", volume, allow_loud?}` | PATCH device volume (clamped to `max_volume` unless `allow_loud`) |

## Auth

Every `/api/*` request must carry the access token in an `X-Soundboard-Token` header
(or `Authorization: Bearer …`). The token is `SOUNDBOARD_TOKEN` from the environment; if
that is unset the app generates one on first start, stores it in `.soundboard_token`
(git-ignored, mode 0600) and prints a ready-to-open `http://…/?token=…` URL. The UI stores
the token in `localStorage` and strips it from the address bar. Because the token travels
in a custom header, cross-site pages cannot forge requests (no CSRF), and POST bodies must
be `application/json`. `SOUNDBOARD_AUTH=off` disables the check for setups that rely on a
reverse proxy / network ACL instead (not recommended).

## Data flow examples

* **Preset tap** → `POST /api/speak` → optional `PATCH /speakers/{horn}` volume →
  `POST /automations/run` (native TTS) → horn speaks.
* **Sound tap** → `POST /api/play-sound` → path validated inside `sounds/` → stream lock →
  ffmpeg transcode → WSS frames paced in real time → returns frame count when done
  (the HTTP request blocks for the length of the clip).
* **Siren** → `POST /api/siren` → optional `PATCH /sirens/{id}` volume →
  `PATCH /sirens/{id}` `{"sirenStatus":{"isActive":true,"duration":ms}}`.
