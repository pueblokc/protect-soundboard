# Hacker News  (Show HN)

**Title:**
> Show HN: Reverse-engineering the UniFi Protect talkback protocol to play any audio on a camera-speaker

**URL:** https://github.com/pueblokc/protect-soundboard

---

**First comment (post this yourself right after submitting):**

Author here. I have a UniFi Protect "AI Horn" speaker and wanted to make it speak custom text and play sounds from my own code. Protect has no clean media/TTS endpoint, and the automation-based tricks are limited and partly owner-account gated.

The interesting find: the mobile app's push-to-talk uses a WebSocket "talkback" channel, and you can drive it yourself. Open `wss://<nvr>/proxy/protect/ws/talkback?speaker=<id>`, then stream AAC-LC ADTS frames (24 kHz mono) as binary messages. The NVR relays each frame to the speaker over UDP. That lets you play *arbitrary* audio: a file, or the mp3 bytes from a TTS API. No automation object, no owner login, no 2FA.

The fiddly part is pacing. Each AAC frame is ~42.7 ms of audio and the speaker has a ~0.5 s jitter buffer. Deliver slightly ahead of real time to keep the buffer full, but burst the whole file and it overruns and garbles. On Windows I also had to bump the timer resolution (default sleep granularity is ~15.6 ms, coarser than a frame) and avoid GC pauses mid-stream.

Honest caveat: on Protect 7.1.x the talkback stream still garbles intermittently even after all that, and it's host-independent (reproduces from a clean idle machine). So for speech I default to native TTS instead (`POST /automations/run` with `PLAY_TEXT_ON_SPEAKER` — robotic but rock-solid clean), and use talkback only for sound-effect files. Two unverified leads toward a garble-free file path are in the writeup: the horn's native talkback format is actually Opus (not the AAC I send), and the speaker exposes an on-device clip list (`audioList` / stored files) implying a native "play stored clip" action.

The repo has a standalone how-to (`docs/CONTROLLING-UNIFI-PROTECT-AUDIO.md`) with the auth, device discovery, talkback streaming, native vs. premium TTS, the garble findings, and siren/volume calls, plus a small self-hosted PWA that wraps it all.

Usual caveat: it's a private/undocumented API, verified on Protect 7.x, and Ubiquiti can change it. Not affiliated with Ubiquiti. MIT licensed.

Happy to go deeper on the framing/pacing or the auth flow.
