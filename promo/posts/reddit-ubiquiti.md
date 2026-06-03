# r/Ubiquiti  (cross-post to r/UNIFI)

**Title:**
> I reverse-engineered the UniFi Protect "talkback" protocol so you can make the AI Horn speak/play anything from code (TTS, sound effects, siren) [open-source + writeup]

**Flair:** Projects / Show & Tell

---

**Body:**

I have an AI Theta / AI Horn speaker and a PoE siren on my Protect setup, and I wanted to trigger them from my own scripts: speak custom text, play a sound, fire the siren from my phone. The official app and automations don't really let you do live, arbitrary audio, and most of the "play text on speaker" automation tricks are limited or owner-account gated.

So I dug into how the mobile app's push-to-talk actually works and wrote it up. Turns out you can open the Protect **talkback WebSocket** and stream your own AAC-LC frames straight to the speaker, no automation and no owner login required.

I packaged two things:

**1. A how-to doc** (the part most people will actually want) that explains, with copy-paste Python:
- authenticating + finding your speaker/siren device IDs
- streaming **any** audio (a file, or TTS bytes) to the speaker over the talkback WS
- speaking text with the **free built-in** Protect voice (the "Test Alarm" path)
- speaking text with a **premium** TTS voice (ElevenLabs, streamed via talkback)
- siren on/off and volume via the private API

**2. A small self-hosted web app** ("Protect Soundboard"): a mobile-first PWA with one-tap message presets, a sounds folder, a siren button, and volume sliders. Drop an mp3 in a folder and it becomes a button.

Repo + doc: **https://github.com/pueblokc/protect-soundboard**
The standalone guide: `docs/CONTROLLING-UNIFI-PROTECT-AUDIO.md`

**Honesty up front:** this uses Protect's private/undocumented endpoints. It's verified on Protect 7.x, but Ubiquiti can change it any time, so treat it as best-effort, not a supported integration. I run it on my LAN behind a reverse proxy that only allows private + VPN source IPs. Use a dedicated local admin account, not your main login.

Happy to answer questions on the talkback framing/pacing, that was the fiddly part (frames have to be paced ~42.7 ms apart or the audio garbles).

*(Built by KCCS / kccsonline.com, MIT licensed.)*
