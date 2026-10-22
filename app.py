"""Protect Soundboard — stream anything to the UniFi Protect AI Horn.

- Custom text / message presets -> native UniFi Protect TTS (default), or opt-in
  ElevenLabs TTS streamed via talkback (tts_mode in config.json).
- Sound effects -> audio files in sounds/ streamed via talkback.
- Siren trigger + volume -> direct private API.

Every /api/* call requires the access token (SOUNDBOARD_TOKEN, or an auto-generated
one stored in .soundboard_token) — see ARCHITECTURE.md "Auth".
"""
import hmac
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from protect_engine import ProtectEngine
from talkback import Talkback
import tts

BASE = Path(__file__).parent
SOUNDS = BASE / "sounds"
SOUNDS.mkdir(exist_ok=True)
PRESETS_FILE = BASE / "presets.json"
TOKEN_FILE = BASE / ".soundboard_token"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MAX_TEXT = 500          # chars per spoken message (Protect TTS + ElevenLabs quota)
MAX_PRESETS = 100


def _load_env(path):
    """Minimal .env loader (dev); real environment variables always win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            os.environ.setdefault(k.strip(), v)


_load_env(BASE / ".env")

CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

app = Flask(__name__)
_engine = None
_tb = None
_init_lock = threading.Lock()
_stream_lock = threading.Lock()  # one talkback stream at a time; overlapping streams garble


class BadRequest(Exception):
    pass


def _nvr_host():
    return os.environ.get("PROTECT_NVR_HOST", CFG["host"])


def _creds():
    user, pw = os.environ.get("PROTECT_NVR_USER"), os.environ.get("PROTECT_NVR_PASS")
    if not user or not pw:
        raise RuntimeError("PROTECT_NVR_USER / PROTECT_NVR_PASS are not set (see .env.example)")
    return user, pw


def engine():
    global _engine
    with _init_lock:
        if _engine is None:
            e = ProtectEngine(_nvr_host(), *_creds(), CFG)
            e.login()
            _engine = e
    return _engine


def talkback():
    global _tb
    with _init_lock:
        if _tb is None:
            _tb = Talkback(_nvr_host(), *_creds(), ffmpeg=FFMPEG)
    return _tb


# ---------------------------------------------------------------- auth

def _auth_disabled():
    return os.environ.get("SOUNDBOARD_AUTH", "").strip().lower() in ("off", "0", "false", "no")


def _load_or_create_token():
    tok = os.environ.get("SOUNDBOARD_TOKEN", "").strip()
    if tok:
        return tok
    try:
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except FileNotFoundError:
        pass
    tok = secrets.token_urlsafe(24)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tok + "\n")
    return tok


ACCESS_TOKEN = None if _auth_disabled() else _load_or_create_token()


def _presented_token():
    tok = request.headers.get("X-Soundboard-Token", "")
    if not tok:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:]
    return tok.strip()


@app.before_request
def _guard_api():
    if not request.path.startswith("/api/"):
        return None
    if ACCESS_TOKEN is not None:
        if not hmac.compare_digest(_presented_token().encode(), ACCESS_TOKEN.encode()):
            return jsonify({"ok": False, "error": "unauthorized", "auth": True}), 401
    if request.method == "POST" and not request.is_json:
        # application/json is not a CORS "simple" type, so cross-site pages can't send it
        return jsonify({"ok": False, "error": "Content-Type must be application/json"}), 415
    return None


@app.after_request
def _headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.errorhandler(BadRequest)
def _bad_request(ex):
    return jsonify({"ok": False, "error": str(ex)}), 400


# ---------------------------------------------------------------- input helpers

def _body():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise BadRequest("JSON object body required")
    return d


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _int(v, name):
    if isinstance(v, bool):
        raise BadRequest(f"{name} must be a number")
    try:
        return int(float(v))
    except (TypeError, ValueError):
        raise BadRequest(f"{name} must be a number") from None


def _sound_path(name):
    """Resolve a sound name to a file inside sounds/, or None (blocks traversal)."""
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or "\x00" in name:
        return None
    f = (SOUNDS / name).resolve()
    if f.parent != SOUNDS.resolve() or not f.is_file() or f.suffix.lower() not in AUDIO_EXT:
        return None
    return f


def _validate_presets(d):
    if not isinstance(d, dict) or not isinstance(d.get("tts"), list):
        raise BadRequest('presets must be {"tts": [...]}')
    if len(d["tts"]) > MAX_PRESETS:
        raise BadRequest(f"at most {MAX_PRESETS} presets")
    out = []
    for i, p in enumerate(d["tts"]):
        if not isinstance(p, dict):
            raise BadRequest(f"preset {i} must be an object")
        label, text = p.get("label"), p.get("text")
        if not isinstance(label, str) or not label.strip() or len(label) > 60:
            raise BadRequest(f"preset {i}: label must be 1-60 chars")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise BadRequest(f"preset {i}: text must be 1-{MAX_TEXT} chars")
        item = {"label": label.strip(), "text": text.strip()}
        icon, color = p.get("icon"), p.get("color")
        if icon is not None:
            if not isinstance(icon, str) or len(icon) > 16:
                raise BadRequest(f"preset {i}: icon must be a short string")
            item["icon"] = icon
        if color is not None:
            if not isinstance(color, str) or not _is_hex_color(color):
                raise BadRequest(f"preset {i}: color must be #rgb or #rrggbb")
            item["color"] = color
        out.append(item)
    res = {"tts": out}
    if isinstance(d.get("_comment"), str):
        res = {"_comment": d["_comment"], **res}
    return res


def _is_hex_color(c):
    return len(c) in (4, 7) and c[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in c[1:])


def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_presets():
    try:
        d = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"tts": []}
    return d if isinstance(d, dict) and isinstance(d.get("tts"), list) else {"tts": []}


# ---------------------------------------------------------------- routes

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    try:
        st = engine().get_status()
        st["tts"] = {"elevenlabs": tts.available(), "mode": CFG.get("tts_mode")}
        return jsonify(st)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/presets")
def api_presets():
    return jsonify(load_presets())


@app.route("/api/presets", methods=["POST"])
def api_save_presets():
    data = _validate_presets(_body())
    _atomic_write(PRESETS_FILE, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})


@app.route("/api/sounds")
def api_sounds():
    files = sorted(f.name for f in SOUNDS.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT)
    return jsonify({"sounds": files})


def _set_horn_vol(d):
    if d.get("volume") is not None:
        engine().set_volume("horn", _int(d["volume"], "volume"), allow_loud=_bool(d.get("allow_loud")))


def _stream(src):
    if not _stream_lock.acquire(blocking=False):
        return None
    try:
        return talkback().stream(CFG["devices"]["horn_id"], src)
    finally:
        _stream_lock.release()


BUSY = ({"ok": False, "error": "horn is busy playing another clip"}, 409)


@app.route("/api/speak", methods=["POST"])
def api_speak():
    d = _body()
    text = d.get("text")
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        return jsonify({"ok": False, "error": "No text"}), 400
    if len(text) > MAX_TEXT:
        return jsonify({"ok": False, "error": f"Text too long (max {MAX_TEXT} chars)"}), 400
    try:
        _set_horn_vol(d)
        # 'unifi' (native Protect TTS) is the clean default. ElevenLabs is streamed
        # via talkback, which garbles on Protect 7.1.x — opt in with tts_mode.
        mode = CFG.get("tts_mode", "unifi")
        # premium (opt-in): ElevenLabs -> talkback
        if mode != "unifi" and tts.available():
            try:
                mp3 = tts.synthesize(text)
                frames = _stream(mp3)
                if frames is None:
                    return jsonify(BUSY[0]), BUSY[1]
                return jsonify({"ok": True, "engine": "elevenlabs", "frames": frames})
            except Exception as ex:
                if mode == "elevenlabs":
                    return jsonify({"ok": False, "error": f"elevenlabs: {ex}"}), 500
                # else fall through to unifi
        r = engine().speak_unifi(text)
        r["engine"] = "unifi"
        return jsonify(r)
    except BadRequest:
        raise
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@app.route("/api/play-sound", methods=["POST"])
def api_play_sound():
    d = _body()
    f = _sound_path(d.get("file"))
    if f is None:
        return jsonify({"ok": False, "error": "Unknown sound"}), 400
    try:
        _set_horn_vol(d)
        frames = _stream(str(f))
        if frames is None:
            return jsonify(BUSY[0]), BUSY[1]
        return jsonify({"ok": True, "frames": frames})
    except BadRequest:
        raise
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@app.route("/api/siren", methods=["POST"])
def api_siren():
    d = _body()
    action = d.get("action", "activate")
    if action not in ("activate", "stop"):
        return jsonify({"ok": False, "error": "action must be 'activate' or 'stop'"}), 400
    duration = _int(d.get("duration", 10), "duration")
    volume = None if d.get("volume") is None else _int(d["volume"], "volume")
    try:
        e = engine()
        if action == "stop":
            return jsonify(e.stop_siren())
        return jsonify(e.activate_siren(duration, volume, _bool(d.get("allow_loud"))))
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@app.route("/api/volume", methods=["POST"])
def api_volume():
    d = _body()
    target = d.get("target", "horn")
    if target not in ("horn", "siren"):
        return jsonify({"ok": False, "error": "target must be 'horn' or 'siren'"}), 400
    volume = _int(d.get("volume", 30), "volume")
    try:
        ok, applied = engine().set_volume(target, volume, _bool(d.get("allow_loud")))
        return jsonify({"ok": ok, "volume": applied})
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("SOUNDBOARD_PORT", CFG.get("port", 5123)))
    bind = os.environ.get("SOUNDBOARD_BIND", "0.0.0.0")
    print(f"Protect Soundboard :{port}  NVR={_nvr_host()}  EL={tts.available()}")
    if ACCESS_TOKEN is None:
        print("WARNING: SOUNDBOARD_AUTH=off — anyone who can reach this port can fire the siren.")
    else:
        src = "SOUNDBOARD_TOKEN" if os.environ.get("SOUNDBOARD_TOKEN") else str(TOKEN_FILE)
        print(f"Access token from {src}. Open once on each device to sign in:")
        print(f"  http://<this-host>:{port}/?token={ACCESS_TOKEN}")
    app.run(host=bind, port=port, threaded=True)
