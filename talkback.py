"""
Talkback streaming to a UniFi Protect speaker (verified on Protect 7.1.60).

Streams ARBITRARY audio (any file, or TTS bytes) to the AI Horn — no owner,
no 2FA, no automation, no frozen content. See Obsidian reference note.

Flow: login (TOKEN cookie) -> open WS /ws/talkback?speaker=<id> (arms device)
-> send frame-aligned AAC-LC ADTS (24kHz mono) as binary WS frames, paced
~42.7ms/frame -> NVR relays each frame as UDP to the horn:7004.
"""
import gc
import os
import ssl
import subprocess
import tempfile
import time
import requests
import urllib3
import websocket

LEAD = 0.40  # seconds pre-buffered in the horn to absorb send hitches (buffer ~0.5-0.6s; >0.5 starts dropping)


def _win_timer(on):
    """Raise the Windows scheduler timer resolution to 1ms so sleeps are precise
    (default ~15.6ms granularity makes frame pacing jittery)."""
    try:
        import ctypes
        fn = ctypes.windll.winmm.timeBeginPeriod if on else ctypes.windll.winmm.timeEndPeriod
        fn(1)
    except Exception:
        pass


def _win_priority(high):
    """Boost this process to HIGH priority during a stream so a busy host
    (Plex, etc.) can't preempt the frame sender mid-playback."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00000080 if high else 0x00000020)
    except Exception:
        pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


FRAME_DT = 1024 / 24000.0  # AAC-LC frame duration at 24kHz mono


def transcode_to_adts(path, ffmpeg="ffmpeg"):
    """Transcode a file to AAC-LC ADTS (24kHz mono) — full output bytes."""
    p = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path,
         # ~700ms leading silence so the horn's post-arm warmup clips silence,
         # not the first word; downmix + 24kHz mono AAC-LC for the talkback codec.
         "-af", "adelay=900:all=1",
         "-c:a", "aac", "-profile:a", "aac_low", "-ar", "24000", "-ac", "1",
         "-b:a", "48k", "-f", "adts", "pipe:1"],
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg transcode failed: {p.stderr.decode(errors='replace')[:300]}")
    return p.stdout


def split_adts(buf):
    """Split ADTS bytes into a list of complete frames."""
    out, i, n = [], 0, len(buf)
    while i + 7 <= n:
        if buf[i] != 0xFF or (buf[i + 1] & 0xF0) != 0xF0:
            i += 1
            continue
        flen = ((buf[i + 3] & 0x03) << 11) | (buf[i + 4] << 3) | (buf[i + 5] >> 5)
        if flen < 7 or i + flen > n:
            break
        out.append(buf[i:i + flen])
        i += flen
    return out


def _sleep_until(target):
    """High-resolution wait until perf_counter() >= target (Windows sleep is coarse)."""
    while True:
        rem = target - time.perf_counter()
        if rem <= 0:
            return
        if rem > 0.004:
            time.sleep(rem - 0.003)
        # else spin the last few ms for smooth frame spacing


class Talkback:
    def __init__(self, host, username, password, ffmpeg="ffmpeg"):
        self.host = host
        self.username = username
        self.password = password
        self.ffmpeg = ffmpeg
        self._tok = None

    def _token(self, force=False):
        if self._tok and not force:
            return self._tok
        s = requests.Session()
        s.verify = False
        for attempt in range(2):
            s.post(f"https://{self.host}/api/auth/login",
                   json={"username": self.username, "password": self.password}, timeout=15)
            tok = s.cookies.get("TOKEN")
            if tok:
                self._tok = tok
                return tok
            time.sleep(0.6)
        raise RuntimeError("Talkback login failed (no TOKEN)")

    def stream(self, speaker_id, src_path_or_bytes):
        """Stream audio to the speaker as smoothly-paced AAC-LC ADTS frames.
        Pre-transcodes, then sends one frame per WS binary message on a precise
        42.7ms schedule (perf_counter + brief spin) — steady spacing is what the
        horn's jitter buffer needs; bursty delivery garbles it. Blocks until done."""
        tmp = None
        if isinstance(src_path_or_bytes, (bytes, bytearray)):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.write(src_path_or_bytes)
            tmp.close()
            path = tmp.name
        else:
            path = src_path_or_bytes

        try:
            frames = split_adts(transcode_to_adts(path, self.ffmpeg))
            if not frames:
                raise RuntimeError("No ADTS frames produced from audio")
            url = f"wss://{self.host}/proxy/protect/ws/talkback?speaker={speaker_id}"
            ws = None
            for attempt in range(2):  # cached token may have expired -> re-login once
                try:
                    ws = websocket.create_connection(
                        url, header=[f"Cookie: TOKEN={self._token(force=attempt > 0)}"],
                        sslopt={"cert_reqs": ssl.CERT_NONE}, origin=f"https://{self.host}", timeout=15)
                    break
                except websocket.WebSocketBadStatusException:
                    self._tok = None
                    if attempt:
                        raise
            gc_was = gc.isenabled()
            gc.disable()  # no GC pauses mid-stream (they cause late frames -> garble)
            _win_timer(True)
            _win_priority(True)
            try:
                time.sleep(0.4)  # let startSinkStream arm the device
                t0 = time.perf_counter()
                # send each frame LEAD seconds ahead of its real-time slot, keeping a
                # small cushion pre-buffered in the horn so brief OS/network hitches
                # don't underrun. First few frames burst out to fill the cushion.
                for n, fr in enumerate(frames):
                    _sleep_until(t0 + n * FRAME_DT - LEAD)
                    ws.send_binary(fr)
                time.sleep(0.3)  # let the tail drain before closing
            finally:
                _win_priority(False)
                _win_timer(False)
                if gc_was:
                    gc.enable()
                try:
                    ws.close()
                except Exception:
                    pass
            return len(frames)
        finally:
            if tmp:
                try:
                    os.remove(tmp.name)
                except Exception:
                    pass
