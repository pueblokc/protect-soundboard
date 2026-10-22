import json
import threading

import app as app_module


# ---- siren / volume

def test_siren_activate_passes_values(client, fake):
    r = client.jpost("/api/siren", {"action": "activate", "duration": 30, "volume": 50, "allow_loud": False})
    assert r.get_json()["ok"]
    assert fake.engine.calls == [("siren_on", 30, 50, False)]


def test_siren_string_false_is_false(client, fake):
    client.jpost("/api/siren", {"action": "activate", "duration": "5", "allow_loud": "false"})
    assert fake.engine.calls == [("siren_on", 5, None, False)]


def test_siren_bad_input_is_400_not_500(client, fake):
    assert client.jpost("/api/siren", {"action": "activate", "duration": "abc"}).status_code == 400
    assert client.jpost("/api/siren", {"action": "explode"}).status_code == 400
    assert client.jpost("/api/siren", ["not", "an", "object"]).status_code == 400
    assert fake.engine.calls == []


def test_volume_target_validated(client, fake):
    assert client.jpost("/api/volume", {"target": "../x", "volume": 10}).status_code == 400
    r = client.jpost("/api/volume", {"target": "horn", "volume": 25})
    assert r.get_json() == {"ok": True, "volume": 25}
    assert client.jpost("/api/volume", {"target": "horn", "volume": None}).status_code == 400


def test_engine_failure_returns_json_500(client, fake, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Protect auth failed")
    monkeypatch.setattr(fake.engine, "stop_siren", boom)
    r = client.jpost("/api/siren", {"action": "stop"})
    assert r.status_code == 500 and r.get_json()["error"] == "Protect auth failed"


def test_missing_credentials_message(monkeypatch):
    monkeypatch.setattr(app_module, "_engine", None)
    monkeypatch.delenv("PROTECT_NVR_USER", raising=False)
    try:
        app_module.engine()
    except RuntimeError as ex:
        assert "PROTECT_NVR_USER" in str(ex)
    else:
        raise AssertionError("expected RuntimeError")


# ---- speak

def test_speak_unifi_default(client, fake):
    r = client.jpost("/api/speak", {"text": "  hi there ", "volume": 45})
    assert r.get_json()["engine"] == "unifi"
    assert fake.engine.calls == [("volume", "horn", 45, False), ("speak", "hi there")]


def test_speak_rejects_empty_and_long(client, fake):
    assert client.jpost("/api/speak", {"text": "   "}).status_code == 400
    assert client.jpost("/api/speak", {"text": 123}).status_code == 400
    assert client.jpost("/api/speak", {"text": "x" * (app_module.MAX_TEXT + 1)}).status_code == 400
    assert client.jpost("/api/speak", {"text": "hi", "volume": "loud"}).status_code == 400
    assert fake.engine.calls == []


def test_speak_elevenlabs_falls_back_to_unifi(client, fake, monkeypatch):
    monkeypatch.setitem(app_module.CFG, "tts_mode", "elevenlabs_then_unifi")
    monkeypatch.setattr(app_module.tts, "available", lambda: True)

    def fail(text):
        raise RuntimeError("quota")
    monkeypatch.setattr(app_module.tts, "synthesize", fail)
    r = client.jpost("/api/speak", {"text": "hi"})
    assert r.get_json()["engine"] == "unifi"


def test_speak_elevenlabs_streams(client, fake, monkeypatch):
    monkeypatch.setitem(app_module.CFG, "tts_mode", "elevenlabs")
    monkeypatch.setattr(app_module.tts, "available", lambda: True)
    monkeypatch.setattr(app_module.tts, "synthesize", lambda t: b"mp3")
    r = client.jpost("/api/speak", {"text": "hi"})
    assert r.get_json() == {"ok": True, "engine": "elevenlabs", "frames": 42}
    assert fake.tb.streams == [("H1", b"mp3")]


# ---- sounds

def test_sounds_lists_audio_only(client):
    assert client.jget("/api/sounds").get_json() == {"sounds": ["Air Horn.mp3"]}


def test_play_sound_ok(client, fake):
    r = client.jpost("/api/play-sound", {"file": "Air Horn.mp3"})
    assert r.get_json() == {"ok": True, "frames": 42}
    assert fake.tb.streams == [("H1", str((fake.sounds / "Air Horn.mp3").resolve()))]


def test_play_sound_blocks_traversal(client, fake):
    for bad in ["../secret.mp3", str(fake.tmp / "secret.mp3"), "..\\secret.mp3", "notes.txt",
                "missing.mp3", "", None, 5, "Air Horn.mp3\x00"]:
        r = client.jpost("/api/play-sound", {"file": bad})
        assert r.status_code == 400, bad
    assert fake.tb.streams == []


def test_play_sound_busy_returns_409(client, fake):
    assert app_module._stream_lock.acquire(blocking=False)
    try:
        r = client.jpost("/api/play-sound", {"file": "Air Horn.mp3"})
        assert r.status_code == 409
    finally:
        app_module._stream_lock.release()
    assert fake.tb.streams == []


def test_concurrent_streams_are_serialized(client, fake, monkeypatch):
    gate, inside, peak = threading.Event(), [], []

    def slow(speaker, src):
        inside.append(1)
        peak.append(len(inside))
        gate.wait(2)
        inside.pop()
        return 1
    monkeypatch.setattr(fake.tb, "stream", slow)
    codes = []
    t = threading.Thread(target=lambda: codes.append(client.jpost("/api/play-sound", {"file": "Air Horn.mp3"}).status_code))
    t.start()
    while not inside:
        pass
    codes.append(client.jpost("/api/play-sound", {"file": "Air Horn.mp3"}).status_code)
    gate.set()
    t.join()
    assert sorted(codes) == [200, 409]
    assert max(peak) == 1


# ---- presets

GOOD = {"tts": [{"label": "Hi", "icon": "👋", "text": "Hello there", "color": "#a6e3a1"}]}


def test_presets_roundtrip(client, fake):
    assert client.jpost("/api/presets", GOOD).get_json() == {"ok": True}
    assert client.jget("/api/presets").get_json() == GOOD
    assert json.loads(app_module.PRESETS_FILE.read_text(encoding="utf-8")) == GOOD
    assert list(fake.tmp.glob("*.tmp")) == []


def test_presets_invalid_does_not_clobber_file(client, fake):
    client.jpost("/api/presets", GOOD)
    assert client.jpost("/api/presets", None).status_code == 415  # no body
    for bad in [[], {"tts": "x"}, {"tts": [{"label": "", "text": "x"}]},
                {"tts": [{"label": "a", "text": "b", "color": "red;background:url(x)"}]},
                {"tts": [{"label": "<img src=x onerror=alert(1)>" * 3, "text": "b"}]}]:
        r = client.jpost("/api/presets", bad)
        assert r.status_code == 400, bad
    assert client.jget("/api/presets").get_json() == GOOD


def test_corrupt_presets_file_does_not_crash(client, fake):
    app_module.PRESETS_FILE.write_text("{not json", encoding="utf-8")
    assert client.jget("/api/presets").get_json() == {"tts": []}
    app_module.PRESETS_FILE.write_text("null", encoding="utf-8")
    assert client.jget("/api/presets").get_json() == {"tts": []}


def test_repo_presets_file_is_valid():
    data = json.loads((app_module.BASE / "presets.json").read_text(encoding="utf-8"))
    assert app_module._validate_presets(data)["tts"]


# ---- .env loader

def test_env_loader_strips_quotes_and_respects_env(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# c\nA_Q="quoted value"\nexport B_Q=\'single\'\nC_Q=plain=eq\nD_Q=keep\n', encoding="utf-8")
    for k in ("A_Q", "B_Q", "C_Q"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("D_Q", "from-env")
    app_module._load_env(f)
    import os
    assert os.environ["A_Q"] == "quoted value"
    assert os.environ["B_Q"] == "single"
    assert os.environ["C_Q"] == "plain=eq"
    assert os.environ["D_Q"] == "from-env"
