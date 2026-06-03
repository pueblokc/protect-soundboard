# community.ui.com  (UniFi Protect category / Stories)

**Title:**
> Open-source: control a Protect speaker (TTS, audio, siren) over the talkback API + a how-to writeup

**Body:**

Sharing a project for anyone who wants to drive a Protect speaker (AI Horn / AI Theta + PoE siren) programmatically.

I documented how to stream arbitrary audio to a Protect speaker using the **talkback WebSocket** (the push-to-talk channel), which works without an automation or owner login. The writeup includes auth, device discovery via `bootstrap`, the talkback streaming details (AAC-LC 24 kHz mono, frame pacing), the free built-in text-to-speech path, premium TTS, and siren/volume control.

There's also a small self-hosted web app ("Protect Soundboard") that wraps it into a mobile-first UI: message presets, a sounds folder, a siren button, and volume sliders.

**Repo + guide:** https://github.com/pueblokc/protect-soundboard

These are private/undocumented endpoints (verified on Protect 7.x), so this is best-effort and not a supported integration. I'd genuinely welcome an officially supported "play audio / TTS on speaker" API. If a Ubiquiti dev sees this and wants the details of what I found, happy to share.

MIT licensed. Not affiliated with Ubiquiti. Posted in the spirit of community tinkering.
