"""Protect Soundboard — stream anything to the UniFi Protect AI Horn.

- Custom text / message presets -> ElevenLabs TTS (premium) streamed via talkback,
  with free UniFi built-in TTS as fallback.
- Sound effects -> audio files in sounds/ streamed via talkback.
- Siren trigger + volume -> direct private API.
"""
import json
import os
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from protect_engine import ProtectEngine
from talkback import Talkback
import tts

BASE = Path(__file__).parent
SOUNDS = BASE / "sounds"
SOUNDS.mkdir(exist_ok=True)
PRESETS_FILE = BASE / "presets.json"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

# load .env (dev); in prod, set these vars in your service manager's environment
_env = BASE / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

app = Flask(__name__)
_engine = None
_tb = None


def engine():
    global _engine
    if _engine is None:
        _engine = ProtectEngine(os.environ.get("PROTECT_NVR_HOST", CFG["host"]),
                                os.environ["PROTECT_NVR_USER"], os.environ["PROTECT_NVR_PASS"], CFG)
        _engine.login()
    return _engine


def talkback():
    global _tb
    if _tb is None:
        _tb = Talkback(os.environ.get("PROTECT_NVR_HOST", CFG["host"]),
                       os.environ["PROTECT_NVR_USER"], os.environ["PROTECT_NVR_PASS"], ffmpeg=FFMPEG)
    return _tb


def load_presets():
    return json.loads(PRESETS_FILE.read_text(encoding="utf-8")) if PRESETS_FILE.exists() else {"tts": []}


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
    PRESETS_FILE.write_text(json.dumps(request.get_json(force=True), indent=2, ensure_ascii=False), encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/sounds")
def api_sounds():
    files = sorted(f.name for f in SOUNDS.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT)
    return jsonify({"sounds": files})


def _set_horn_vol(d):
    if d.get("volume") is not None:
        engine().set_volume("horn", int(d["volume"]), allow_loud=bool(d.get("allow_loud")))


@app.route("/api/speak", methods=["POST"])
def api_speak():
    d = request.get_json(force=True)
    text = (d.get("text") or "").strip()
    if not text:
        return jsonify({"error": "No text"}), 400
    try:
        _set_horn_vol(d)
        # 'unifi' (native Protect TTS) is the clean default. ElevenLabs is streamed
        # via talkback, which garbles on Protect 7.1.x — opt in with tts_mode.
        mode = CFG.get("tts_mode", "unifi")
        # premium (opt-in): ElevenLabs -> talkback
        if mode != "unifi" and tts.available():
            try:
                mp3 = tts.synthesize(text)
                frames = talkback().stream(CFG["devices"]["horn_id"], mp3)
                return jsonify({"ok": True, "engine": "elevenlabs", "frames": frames})
            except Exception as ex:
                if mode == "elevenlabs":
                    return jsonify({"ok": False, "error": f"elevenlabs: {ex}"}), 500
                # else fall through to unifi
        r = engine().speak_unifi(text)
        r["engine"] = "unifi"
        return jsonify(r)
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@app.route("/api/play-sound", methods=["POST"])
def api_play_sound():
    d = request.get_json(force=True)
    name = d.get("file", "")
    f = SOUNDS / name
    if not name or not f.is_file() or f.suffix.lower() not in AUDIO_EXT:
        return jsonify({"error": "Unknown sound"}), 400
    try:
        _set_horn_vol(d)
        frames = talkback().stream(CFG["devices"]["horn_id"], str(f))
        return jsonify({"ok": True, "frames": frames})
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@app.route("/api/siren", methods=["POST"])
def api_siren():
    d = request.get_json(force=True)
    try:
        e = engine()
        if d.get("action") == "stop":
            return jsonify(e.stop_siren())
        return jsonify(e.activate_siren(int(d.get("duration", 10)), d.get("volume"), bool(d.get("allow_loud"))))
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@app.route("/api/volume", methods=["POST"])
def api_volume():
    d = request.get_json(force=True)
    try:
        ok, applied = engine().set_volume(d.get("target", "horn"), int(d.get("volume", 30)), bool(d.get("allow_loud")))
        return jsonify({"ok": ok, "volume": applied})
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


if __name__ == "__main__":
    print(f"Protect Soundboard :{CFG.get('port',5123)}  NVR={os.environ.get('PROTECT_NVR_HOST',CFG['host'])}  EL={tts.available()}")
    app.run(host="0.0.0.0", port=CFG.get("port", 5123), threaded=True)
