import pytest

import tts


class R:
    def __init__(self, code, content=b"", text=""):
        self.status_code, self.content, self.text = code, content, text


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "CACHE", tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    return tmp_path


def test_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert not tts.available()
    with pytest.raises(RuntimeError):
        tts.synthesize("hi")


def test_synthesize_caches(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(tts.requests, "post", lambda *a, **k: calls.append(1) or R(200, b"MP3"))
    assert tts.synthesize("hi") == b"MP3"
    assert tts.synthesize("hi") == b"MP3"
    assert len(calls) == 1
    assert [p.suffix for p in cache.iterdir()] == [".mp3"]  # no leftover .part files


def test_error_not_cached(cache, monkeypatch):
    monkeypatch.setattr(tts.requests, "post", lambda *a, **k: R(401, text="bad key"))
    with pytest.raises(RuntimeError, match="ElevenLabs 401"):
        tts.synthesize("hi")
    monkeypatch.setattr(tts.requests, "post", lambda *a, **k: R(200, b""))
    with pytest.raises(RuntimeError, match="empty"):
        tts.synthesize("hi")
    assert list(cache.iterdir()) == []
