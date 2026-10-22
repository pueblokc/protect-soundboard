"""Text-to-speech for the soundboard.

Primary: ElevenLabs (premium voice) -> mp3 bytes, cached on disk by hash.
The app streams the mp3 to the horn via talkback. If ElevenLabs is unavailable
(no key / quota / error), the caller can fall back to UniFi's built-in TTS
(POST /automations/run) which is free + unlimited but a plainer voice.
"""
import hashlib
import os
import tempfile
from pathlib import Path
import requests

CACHE = Path(__file__).parent / "tts_cache"
CACHE.mkdir(exist_ok=True)

def _key():
    return os.environ.get("ELEVENLABS_API_KEY", "")


def _voice():
    return os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # "Sarah"


def _model():
    return os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5")


def available():
    return bool(_key())


def synthesize(text):
    """Return mp3 bytes for text (ElevenLabs), cached by hash. Raises on failure."""
    EL_KEY, EL_VOICE, EL_MODEL = _key(), _voice(), _model()
    if not EL_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    key = hashlib.sha256(f"{EL_VOICE}|{EL_MODEL}|{text}".encode()).hexdigest()[:32]
    f = CACHE / f"{key}.mp3"
    if f.exists() and f.stat().st_size > 0:
        return f.read_bytes()
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE}",
        headers={"xi-api-key": EL_KEY, "accept": "audio/mpeg", "content-type": "application/json"},
        json={"text": text, "model_id": EL_MODEL,
              "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    if not r.content:
        raise RuntimeError("ElevenLabs returned empty audio")
    # write-then-rename so a crash or concurrent request never leaves a truncated
    # mp3 that the size>0 check above would serve from cache forever
    fd, tmp = tempfile.mkstemp(dir=CACHE, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(r.content)
        os.replace(tmp, f)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return r.content
