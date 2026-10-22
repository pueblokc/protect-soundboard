import os
import shutil
from pathlib import Path

import pytest

import talkback

ROOT = Path(__file__).resolve().parent.parent


def adts_frame(payload_len):
    flen = 7 + payload_len
    hdr = bytes([0xFF, 0xF1, 0x60, 0x40 | ((flen >> 11) & 0x03), (flen >> 3) & 0xFF, ((flen & 0x07) << 5) | 0x1F, 0xFC])
    return hdr + bytes(payload_len)


def test_split_adts_frames():
    a, b = adts_frame(10), adts_frame(200)
    assert talkback.split_adts(a + b) == [a, b]


def test_split_adts_skips_garbage_and_drops_truncated_tail():
    a = adts_frame(20)
    frames = talkback.split_adts(b"\x00\x12junk" + a + adts_frame(50)[:30])
    assert frames == [a]


def test_split_adts_empty():
    assert talkback.split_adts(b"") == []
    assert talkback.split_adts(b"\xff\xf1") == []


def test_missing_ffmpeg_is_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        talkback.transcode_to_adts(str(tmp_path / "x.mp3"), ffmpeg=str(tmp_path / "no-ffmpeg"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_transcode_real_sound_produces_frames():
    frames = talkback.split_adts(talkback.transcode_to_adts(str(ROOT / "sounds" / "Alarm Beep.mp3")))
    assert len(frames) > 10
    assert all(f[0] == 0xFF for f in frames)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_transcode_bad_input_raises(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    with pytest.raises(RuntimeError, match="ffmpeg transcode failed"):
        talkback.transcode_to_adts(str(bad))


class FakeWS:
    def __init__(self):
        self.sent, self.closed = [], False

    def send_binary(self, b):
        self.sent.append(b)

    def close(self):
        self.closed = True


def _fast(monkeypatch, frames):
    monkeypatch.setattr(talkback, "transcode_to_adts", lambda p, f: b"".join(frames))
    monkeypatch.setattr(talkback, "_sleep_until", lambda t: None)
    monkeypatch.setattr(talkback.time, "sleep", lambda s: None)


def test_stream_sends_every_frame_and_cleans_up(monkeypatch):
    frames = [adts_frame(9) for _ in range(5)]
    _fast(monkeypatch, frames)
    ws, seen = FakeWS(), {}

    def connect(url, **kw):
        seen["url"], seen["hdr"] = url, kw["header"]
        return ws
    monkeypatch.setattr(talkback.websocket, "create_connection", connect)
    tb = talkback.Talkback("nvr.example", "u", "p")
    tb._tok = "TOK"
    tmp_paths = []
    real_ntf = talkback.tempfile.NamedTemporaryFile

    def ntf(**kw):
        f = real_ntf(**kw)
        tmp_paths.append(f.name)
        return f
    monkeypatch.setattr(talkback.tempfile, "NamedTemporaryFile", ntf)
    assert tb.stream("SPK", b"mp3bytes") == 5
    assert ws.sent == frames and ws.closed
    assert seen["url"] == "wss://nvr.example/proxy/protect/ws/talkback?speaker=SPK"
    assert seen["hdr"] == ["Cookie: TOKEN=TOK"]
    assert tmp_paths and not os.path.exists(tmp_paths[0])  # temp mp3 removed
    import gc
    assert gc.isenabled()


def test_stream_relogs_in_on_bad_status(monkeypatch):
    _fast(monkeypatch, [adts_frame(9)])
    attempts = []

    def connect(url, **kw):
        attempts.append(kw["header"])
        if len(attempts) == 1:
            raise talkback.websocket.WebSocketBadStatusException("401", 401)
        return FakeWS()
    monkeypatch.setattr(talkback.websocket, "create_connection", connect)
    tb = talkback.Talkback("h", "u", "p")
    tb._tok = "OLD"
    monkeypatch.setattr(tb, "_token", lambda force=False: "NEW" if force else (tb._tok or "NEW"))
    assert tb.stream("SPK", "x.mp3") == 1
    assert attempts[1] == ["Cookie: TOKEN=NEW"]


def test_stream_no_frames_raises(monkeypatch):
    _fast(monkeypatch, [])
    with pytest.raises(RuntimeError, match="No ADTS frames"):
        talkback.Talkback("h", "u", "p").stream("SPK", "x.mp3")
