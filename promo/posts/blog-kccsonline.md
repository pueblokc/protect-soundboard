---
title: "Making a UniFi Protect Speaker Talk: Reverse-Engineering the Talkback Protocol"
author: KCCS
date: 2026-06-03
tags: [unifi, unifi-protect, home-lab, reverse-engineering, open-source]
---

# Making a UniFi Protect Speaker Talk

> TL;DR: We open-sourced **Protect Soundboard**, a self-hosted app that makes a UniFi Protect AI Horn
> speak custom text, play sound effects, and sound the siren from your phone, plus a full how-to doc on
> the underlying mechanism. **[github.com/pueblokc/protect-soundboard](https://github.com/pueblokc/protect-soundboard)**

## The problem

UniFi Protect speakers (the AI Horn, AI Theta with the speaker, etc.) can clearly play audio: the app does
push-to-talk, and automations can "play text on speaker." But if you want to do it from *your own code*, live,
with arbitrary audio or a good text-to-speech voice, you hit a wall. There's no clean, documented media or TTS
endpoint, and the automation-based approaches are limited and partly tied to the owner account.

## The find

The mobile app's push-to-talk runs over a **talkback WebSocket**. You can drive it yourself:

1. Log in, grab the session token.
2. Open `wss://<nvr>/proxy/protect/ws/talkback?speaker=<id>`.
3. Stream **AAC-LC ADTS** audio (24 kHz, mono) as binary WebSocket frames, one frame per message, paced
   about 42.7 ms apart.
4. The NVR relays each frame to the speaker as UDP.

That's it. No automation object, no owner login, no 2FA. You can feed it a file or the mp3 bytes from any TTS
engine, which is how we get a natural-sounding premium voice.

The fiddly part is **pacing**. Each AAC frame is ~42.7 ms of audio, and the speaker keeps a small (~0.5 s)
jitter buffer. Deliver a touch ahead of real time to keep the buffer full, but burst the whole file at once and
it overruns and garbles. On Windows we also raise the timer resolution (default sleep granularity is ~15.6 ms,
coarser than a single frame) and disable GC for the duration so nothing introduces a late frame.

## The honest catch (and the clean path)

Even with all of that, **on Protect 7.1.x the talkback stream still garbles intermittently** — and it's
**host-independent** (we reproduced it from a clean, idle machine, so it isn't CPU load, GC, or timer jitter).
So for **speech we don't rely on talkback at all**: we use **native Protect TTS** — `POST /automations/run`
with a `PLAY_TEXT_ON_SPEAKER` action — which plays the text locally on the speaker, robotic but rock-solid
clean. Talkback stays in the toolbox for arbitrary sound-effect *files*, where it's best-effort. The app
defaults to native TTS for every spoken button.

Two leads we found (in the speaker's own `bootstrap`) toward making *files* clean too: the horn's native
talkback format is actually **Opus**, not the AAC we send (the NVR's transcode is a prime garble suspect), and
the speaker exposes an on-device **audio-clip list**, implying a native "play stored clip" action that would
bypass streaming entirely. Both are unverified — documented in the repo as next steps.

## The three control paths

| Goal | Mechanism | Quality |
|------|-----------|---------|
| Speak text (the reliable path) | `POST /automations/run` → `PLAY_TEXT_ON_SPEAKER` | clean |
| Play an arbitrary audio file | Talkback WebSocket stream | best-effort (may garble on 7.1.x) |
| Siren on/off + volume | Direct `PATCH` on the device | clean |

The repo's [`docs/CONTROLLING-UNIFI-PROTECT-AUDIO.md`](https://github.com/pueblokc/protect-soundboard/blob/master/docs/CONTROLLING-UNIFI-PROTECT-AUDIO.md)
walks through all three with copy-paste Python.

## The app

**Protect Soundboard** wraps it in a mobile-first PWA: type any text and the horn speaks it, tap a preset
("Get Off My Lawn," "You're Being Recorded"), drop an mp3 into a folder to get a new button, trigger the siren,
and ride the volume with a safety cap. Self-hosted, runs on a tiny Flask server, installs to your home screen.

## Caveats

This uses Protect's private/undocumented API. It's verified on Protect 7.x, but Ubiquiti can change it without
notice, so treat it as best-effort. Keep it on your LAN or a private overlay, behind a reverse proxy that
restricts source IPs, and use a dedicated minimal-permission local account rather than your primary login.

Not affiliated with or endorsed by Ubiquiti. MIT licensed.

---

*Built by [KCCS](https://kccsonline.com). We run and automate home-lab and small-business infrastructure.*
