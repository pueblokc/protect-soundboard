# r/homeassistant  (and community.home-assistant.io "Share your Projects!")

**Title:**
> Make a UniFi Protect speaker (AI Horn) play TTS / arbitrary audio / siren from code, the talkback method + a how-to

**Body:**

If you've ever wanted your UniFi Protect speaker to actually *say something* from an automation (announce a person at the door, bark "get off my lawn," sound a siren) and got stuck because Protect doesn't expose a clean TTS/media endpoint, this might help.

I reverse-engineered the Protect **talkback** WebSocket (the same channel the mobile app uses for push-to-talk). You can stream your own audio frames to the speaker over it: a file, or the mp3 bytes from any TTS engine. No owner account, no 2FA dance, no automation object to manage.

I wrote a how-to with copy-paste Python covering:
- auth + discovering your speaker/siren IDs from `bootstrap`
- streaming arbitrary audio over the talkback WS (AAC-LC, 24 kHz mono, paced ~42.7 ms/frame)
- free built-in Protect TTS vs. premium TTS (ElevenLabs) streamed via talkback
- siren on/off + volume over the private API

**Repo + guide:** https://github.com/pueblokc/protect-soundboard
**The standalone doc:** `docs/CONTROLLING-UNIFI-PROTECT-AUDIO.md`

It ships as a standalone Flask PWA, but the doc is written so you can lift the talkback function straight into a `python_script`, a shell command, or a small custom integration / pyscript. If anyone wants to wrap this into a proper `notify`/`tts` platform or an AppDaemon app, the mechanism is all there. I'd love to see it.

**Caveat that matters for clean audio:** on Protect 7.1.x the talkback stream garbles intermittently (host-independent — not just pacing). So for **speech, native Protect TTS** (`PLAY_TEXT_ON_SPEAKER`) is the clean, reliable path and what I default to; talkback is for arbitrary sound-effect files, best-effort. The doc covers both plus two leads toward a garble-free file path (the horn's native talkback format is Opus not AAC; it also has an on-device clip list). All private/undocumented API, verified on Protect 7.x, could break on a Ubiquiti update. Keep it on your LAN/VPN with a dedicated local admin account.

*(MIT, by KCCS.)*
