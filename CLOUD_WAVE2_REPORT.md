# Cloud Wave 2 report: protect-soundboard

Branch: `claude/cloud-wave2-2026-10-22` (based on `master` @ `2ad7c1d`). No PR opened, nothing merged, deployed or tagged.
All file:line references to *original* code point at `2ad7c1d`.

## TL;DR

1. **Anyone could fire the siren.** There was no auth at all, the server binds `0.0.0.0`, and every POST used `get_json(force=True)`. So beyond LAN access, *any web page* opened by someone on your LAN could fire the siren with a cross-site `text/plain` POST (CSRF). Now every `/api/*` call needs an access token in a custom header, and POSTs must be `application/json`. Tests prove that all six control endpoints return 401 without it.
2. **Stored XSS + path traversal fixed.** Preset labels and sound filenames were injected with `innerHTML`, and `/api/presets` accepted any JSON. `/api/play-sound` accepted `../x.mp3` or absolute paths, so any audio file on disk could be streamed.
3. **No leaked secrets.** I checked all tracked files, the full git history, PNG/MP3 metadata, and both screenshots. I found no real IPs, tokens or internal hostnames (only the `192.168.1.100` placeholder). Two minor internal references (an "Obsidian reference note" and "Plex") were scrubbed from `talkback.py` comments.
4. **Reliability fixes:** UniFi OS CSRF-token rotation is now tracked; overlapping talkback streams are serialized (return 409 instead of garbling); preset and TTS-cache writes are atomic; bad input returns 400 instead of 500.
5. **65 pytest tests + ruff + GitHub Actions CI** (Python 3.9/3.12/3.13). Everything passes locally, including in a fresh checkout.

## Commits

| Hash | What |
|------|------|
| `4ea8ef7` | protect_engine: track rotated CSRF tokens, None-safe status, validate volume target, clamp siren duration |
| `d931d16` | tts/talkback: atomic TTS cache writes, clear ffmpeg-missing error, ffmpeg timeout, scrub internal refs |
| `a22d5a6` | Require an access token on every /api call; block CSRF, path traversal and stored XSS (+ validation, stream lock, .env quotes) |
| `b2cefe0` | Add pytest suite (mocked NVR/talkback/ElevenLabs), ruff config; bump requests/flask floors |
| `411d76a` | talkback: rename unused loop variable (ruff B007) |
| `9cce588` | Docs: ARCHITECTURE.md; README covers access token and env vars, and corrects inaccurate claims |
| `e47f134` | CI: ruff + pytest on 3.9/3.12/3.13 |
| (this commit) | CLOUD_WAVE2_REPORT.md |

## Bugs found and fixed

| Original location | Symptom | Fix |
|---|---|---|
| `app.py:99,127,142,154` | `get_json(force=True)` + `d.get(...)`: a JSON array or `null` body caused an `AttributeError` and an HTML 500 (outside the try blocks) | `_body()` requires a JSON object and returns 400 |
| `app.py:94,147,156` | `int("abc")` returned a 500. `bool("false")` is `True`, so a string `"false"` for `allow_loud` *bypassed the volume cap* | `_int()` returns 400 on bad input; `_bool()` parses strings correctly |
| `app.py:82` | `/api/presets` wrote any JSON non-atomically. A body of `null` or a list made `presets.json` unusable for the UI, and a crash mid-write lost the file | Schema validation (label, text, icon, `#hex` color, max 100 presets) plus write-to-temp-then-rename. `load_presets` survives a corrupt file |
| `app.py:39-53` | Lazy `engine()`/`talkback()` singletons weren't thread-safe under `threaded=True`, so concurrent first requests double-logged in. Missing env vars surfaced as a cryptic `'PROTECT_NVR_USER'` | Init lock plus a clear "PROTECT_NVR_USER / PROTECT_NVR_PASS are not set" error |
| `app.py:110,135` | Two simultaneous talkback streams (two taps, or two phones) hit the horn at once and garbled; they also fought over `gc.disable()` and process priority | A single `_stream_lock`; a second request gets `409 horn is busy` |
| `app.py:100` | Unbounded text sent to Protect TTS / ElevenLabs (quota burn) | Max 500 characters |
| `app.py:29` | `.env` values in quotes (`PASS="x"`) kept the quotes, so login failed | Loader strips matching quotes and accepts an `export ` prefix |
| `protect_engine.py:48-56` | UniFi OS rotates the CSRF token (`X-Updated-Csrf-Token`). The engine kept the stale one, so writes could start failing on a long-running instance | Track the rotated token on every response |
| `protect_engine.py:59-66` | `x["id"]` raised `KeyError` on a device without `id`; `sirenStatus: null` raised `AttributeError`; a non-200 bootstrap gave a confusing JSON error | `.get()`, `or {}`, and an explicit "HTTP nnn" error |
| `protect_engine.py:77` | Any `target` other than `"horn"` silently changed the **siren** volume | Raises `ValueError`; the API returns 400 |
| `protect_engine.py:99` | Siren duration unbounded (negative or huge) | Clamped to 1–300 s (the UI offers 5–60) |
| `tts.py:50` | Non-atomic cache write: a crash or concurrent request could leave a truncated mp3 that the `size>0` check then served from cache forever. An empty 200 response was also cached | Temp file + `os.replace`; empty audio raises |
| `talkback.py:53` | No ffmpeg gave the error `[Errno 2] No such file...` (the README called it a "silent fail"). No timeout, so a stuck ffmpeg pinned a request thread | Clear "ffmpeg not found — install it or set FFMPEG" message; 120 s timeout; `-nostdin` |
| `templates/index.html:116` | Volume slider `change` handler had no try/catch (unhandled rejection) and no feedback on failure | Toast on error |
| (new UI code, caught in headless test) | First version of the token prompt stacked prompts and let stale 401s wipe a freshly entered good token | One pending prompt; only clear the token if it's still the one that was rejected; "tap to sign in" banner after Cancel |

Doc fixes (the README claimed things the code doesn't do): presets are **not** editable in-app (no editor exists), so the README now says to edit `presets.json` or use `POST /api/presets`. "Speaks it in a premium voice" is now "native voice by default". The ffmpeg "silent fail" is now described accurately. A `talkback.py` comment said 700 ms of leading silence; the code uses 900 ms.

## Security findings

### Fixed
- **No authentication on any endpoint, including the siren** (`app.py` all routes, bind `0.0.0.0` at `app.py:164`). Token auth now covers every `/api/*` route. Comparison uses `hmac.compare_digest`. The token comes from `SOUNDBOARD_TOKEN`, or is auto-generated into `.soundboard_token` (mode 0600, git-ignored) and printed as a `/?token=` URL at startup. The UI stores it in `localStorage` and removes it from the address bar. `SOUNDBOARD_AUTH=off` is the explicit opt-out.
- **CSRF**: `force=True` accepted `text/plain` cross-site POSTs. POSTs now require `application/json` plus the custom token header, so a cross-origin request needs a CORS preflight, which Flask never grants.
- **Stored XSS** (`templates/index.html:123,128`): buttons are now built with `textContent`. Server-side preset validation was added too (color must be hex, so no CSS injection through `style.background`). Verified in headless Chromium: an `<img onerror>` label renders as text.
- **Path traversal / arbitrary file read into ffmpeg** (`app.py:129`): the name must be a bare filename that resolves inside `sounds/` with an audio extension.
- **Security headers**: `X-Frame-Options: DENY` (prevents clickjacking the siren button), `nosniff`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on the API.
- **Dependencies**: floors raised to `requests>=2.32.4` (CVE-2024-35195, CVE-2024-47081) and `flask>=3.1.1` (CVE-2025-47278).

### Not fixed (needs a decision)
- **Plain HTTP**: the token travels in cleartext on the LAN. The fix is a TLS reverse proxy or Tailscale; this is documented in the README. I didn't add TLS to the app.
- **Flask dev server in production** (`app.run`): it works, but it isn't hardened. Consider `waitress`/`gunicorn`. I left it alone to avoid changing how you deploy.
- **`verify=False` to the NVR** (`protect_engine.py`, `talkback.py`): this is required for the console's self-signed cert. Pinning the cert's fingerprint would be the proper fix (design decision).
- **NVR error text is returned to the client** (`str(ex)`). It may include the NVR host, but only to authenticated users now.
- **`tts_cache/` grows unbounded**: low risk, since it's one small mp3 per unique phrase.
- **Rate limiting** on the siren: none. Token holders can spam it.
- **`tts_mode: "elevenlabs"`** ("premium only") silently uses native TTS when no API key is set. I left this behavior unchanged.
- **Token is accepted only in headers**, not as a query parameter on API calls. That's intentional, so it doesn't end up in proxy logs.

## Tests

```bash
pip install -r requirements-dev.txt      # flask, requests, websocket-client, pytest, ruff
ruff check .
pytest -q
```

- **65 tests, all passing** (Python 3.13 locally; also run from a fresh checkout + fresh venv).
- `tests/test_auth.py`: every control endpoint rejects a missing or wrong token; the `text/plain` CSRF POST is rejected; bearer auth works; token file is 0600 and stable; env token wins.
- `tests/test_api.py`: siren/volume/speak/play-sound validation; traversal attempts; 409 busy and true serialization under real threads; preset validation, atomic write, corrupt-file recovery; `.env` quote parsing.
- `tests/test_protect_engine.py`: CSRF rotation, 401 re-login, volume cap, siren clamp and payloads, None-safe status.
- `tests/test_talkback.py`: ADTS frame splitting (garbage and truncation), real ffmpeg transcode of `sounds/Alarm Beep.mp3` (skipped if ffmpeg is missing), missing-ffmpeg error, fake-WebSocket stream (all frames sent, temp file deleted, GC re-enabled, re-login on bad status).
- `tests/test_tts.py`: caching, no `.part` leftovers, errors and empty audio not cached.
- No network or hardware is used: the NVR, WebSocket and ElevenLabs are all faked.
- I also ran a manual headless-Chromium check of the UI against a mocked backend. Checked: token prompt, cancel banner, `?token=` bootstrap, XSS label rendered as text, siren button sends the token.

CI: `.github/workflows/ci.yml` runs ruff + pytest on 3.9/3.12/3.13 with ffmpeg installed. It needs no secrets and has no deploy step. **Green on GitHub:** run #2 (`1d311b7`) passed on all three Python versions.

## Needs Justin

- **Breaking change on upgrade:** after deploying, every phone or PWA needs the token **once**. Open the `/?token=…` URL printed at startup, or enter it at the prompt. Recommendation: set `SOUNDBOARD_TOKEN` in the service environment rather than relying on `.soundboard_token`, so it survives a re-clone. If you'd rather rely only on network ACLs, `SOUNDBOARD_AUTH=off` restores the old behavior (not recommended).
- **Check on real hardware** (I couldn't reach the NVR):
  - siren on/off and duration (now clamped to 300 s max), and horn/siren volume;
  - native TTS still speaks;
  - a sound effect plays, and a second tap during playback shows "horn is busy";
  - a long-running instance keeps working across a CSRF rotation (leave it up for a day, then fire a volume change).
- **Installed PWA on iOS:** iOS stores home-screen PWAs separately from Safari. Opening `?token=` in Safari doesn't carry over, so enter the token at the in-app prompt (or re-add the PWA from the token URL).
- **Credentials to rotate:** none found committed. Still, if the Protect local account password was ever reused elsewhere, keep it dedicated as the README advises.
- **Before deploy:** `pip install -r requirements.txt` (new minimum versions), make sure `.soundboard_token` is not world-readable on Windows (POSIX 0600 doesn't apply there; the file inherits folder ACLs), and consider a TLS front end.

## Unsure about

- **Siren duration ceiling of 300 s.** I picked a conservative cap; I don't know Protect's own maximum. Easy to change: `MAX_SIREN_SECONDS` in `protect_engine.py`.
- **CSRF header name.** I assumed UniFi OS uses `X-Updated-Csrf-Token` for rotation (this matches common Protect client libraries). It wasn't verified against your 7.1.60 console.
- **`ruff format`.** I didn't adopt it, because it would reformat most files; the lint rules only.
