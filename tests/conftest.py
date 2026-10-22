"""Shared fixtures. No network: the Protect engine and talkback client are fakes."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# must be set before app is imported (ACCESS_TOKEN is resolved at import)
os.environ["SOUNDBOARD_TOKEN"] = "test-token"
os.environ.pop("SOUNDBOARD_AUTH", None)
os.environ.setdefault("PROTECT_NVR_USER", "user")
os.environ.setdefault("PROTECT_NVR_PASS", "pass")
os.environ.pop("ELEVENLABS_API_KEY", None)

import app as app_module  # noqa: E402

TOKEN = "test-token"


class FakeEngine:
    def __init__(self):
        self.calls = []

    def get_status(self):
        self.calls.append(("status",))
        return {"horn": {"name": "Horn", "volume": 40, "connected": True},
                "siren": {"name": "Siren", "volume": 30, "active": False, "connected": True},
                "max_volume": 60}

    def set_volume(self, target, volume, allow_loud=False):
        self.calls.append(("volume", target, volume, allow_loud))
        return True, volume

    def activate_siren(self, duration=10, volume=None, allow_loud=False):
        self.calls.append(("siren_on", duration, volume, allow_loud))
        return {"ok": True, "status": 200}

    def stop_siren(self):
        self.calls.append(("siren_off",))
        return {"ok": True, "status": 200}

    def speak_unifi(self, text):
        self.calls.append(("speak", text))
        return {"ok": True, "status": 200}


class FakeTalkback:
    def __init__(self):
        self.streams = []

    def stream(self, speaker_id, src):
        self.streams.append((speaker_id, src))
        return 42


@pytest.fixture
def fake(monkeypatch, tmp_path):
    eng, tb = FakeEngine(), FakeTalkback()
    monkeypatch.setattr(app_module, "_engine", eng)
    monkeypatch.setattr(app_module, "_tb", tb)
    sounds = tmp_path / "sounds"
    sounds.mkdir()
    (sounds / "Air Horn.mp3").write_bytes(b"ID3fake")
    (sounds / "notes.txt").write_text("x")
    (tmp_path / "secret.mp3").write_bytes(b"outside")
    monkeypatch.setattr(app_module, "SOUNDS", sounds)
    monkeypatch.setattr(app_module, "PRESETS_FILE", tmp_path / "presets.json")
    monkeypatch.setattr(app_module, "CFG", {**app_module.CFG, "tts_mode": "unifi",
                                            "devices": {"horn_id": "H1", "horn_mac": "M", "siren_id": "S1"}})
    return type("Fakes", (), {"engine": eng, "tb": tb, "tmp": tmp_path, "sounds": sounds})


@pytest.fixture
def client(fake):
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()

    def post(path, body, token=TOKEN, **kw):
        headers = {"X-Soundboard-Token": token} if token else {}
        return c.post(path, json=body, headers=headers, **kw)

    def get(path, token=TOKEN):
        return c.get(path, headers={"X-Soundboard-Token": token} if token else {})

    c.jpost, c.jget = post, get
    return c
