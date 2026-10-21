# TechFi Labs — Voice AI Playground

The worker serves a responsive, framework-free HTML/CSS/JavaScript playground
at `http://127.0.0.1:7860`. It uses the existing WebRTC transport and per-session
provider factory. No transcript display, recording, telephony or external assets
were added.

## Voice selection and server configuration

The TTS dropdown defaults to **Sarvam Bulbul V3 — Shubh**, with optional
**Google Chirp 3 HD — Achird** and experimental **Bulbul V4 Flash — Shubh Hindi**.
The LLM dropdown defaults to **GPT-4.1 Mini**, with experimental
**Sarvam 105B Conversations**. Both controls lock while connecting/connected.

The offer body accepts only `sdp`, `type`, `voice_provider` (`sarvam`, `google`,
`sarvam-v4-flash`) and `llm_provider` (`openai`, `sarvam`). Missing selectors use
the original defaults. Unknown selectors and extra fields, including client model,
voice or credential fields, return sanitized HTTP 422 before opening a peer.
Missing selected-provider credentials yield safe HTTP 503; there is no fallback.

Server-owned presets and secure key routing are documented in
[experimental provider configuration](sarvam-experimental.md). Each offer receives
a validated settings snapshot; STT, prompt, language, VAD, watchdogs and timing
remain unchanged. Choices apply only to new sessions. No active-call switching,
automatic fallback, dynamic voice catalogue or browser secrets are introduced.
Update the static display label if changing a server preset voice.

## Startup

Preserve the existing ignored `.env`; do not overwrite configured credentials.
For the Google option, install the optional Google dependencies:

```sh
uv sync --frozen --all-packages --extra google --group dev
uv run --no-sync techfi-voice --check-config
```

Sarvam needs its configured key; Google needs authentication as described in
the [Google setup guide](google-tts.md). Preflight validates only process
configuration and does not create SDK clients, authenticate Google, probe either
preset remotely or verify voice availability. STT setup remains documented in
the [realtime STT guide](sarvam-realtime-stt.md).

For a future authorized manual test, start the worker:

```sh
uv run --no-sync techfi-voice
```

Open `http://127.0.0.1:7860` using a current Chromium browser. Keep the server
local; its loopback/origin checks are not public authentication.

## Manual acceptance checklist

The following live conversation steps can incur provider charges and **were not
executed during this change**. Use synthetic identities and headphones.

1. At desktop and narrow/mobile widths, confirm the page fits without horizontal
   scrolling. Use keyboard Tab/Shift-Tab to reach both labeled selectors, Start,
   End and playback control. Focus should be visible. Check the OS reduced-motion
   setting and screen-reader status announcements.
2. On first load, confirm **Ready**, GPT-4.1 Mini and Bulbul v3 selected, and no microphone prompt.
   Select a model and voice and press **Start Conversation**: status becomes **Connecting**
   and both selectors are disabled. Deny permission to check **Error** and recovery.
3. Start again and allow the microphone. Confirm **Connected**, AI disclosure,
   Hindi/Hinglish input/output, local microphone and assistant activity, and
   unchanged interruption/opt-out behavior. If autoplay is blocked, press Play
   on the provided audio control. Activity meters do not prove audible playback.
4. Press **End Conversation**. Confirm **Disconnected**, the browser microphone
   indicator ends, meters reset, playback clears and both selectors unlock. Also
   end while the permission prompt or connection is pending; late permission
   completion must not start another session.
5. Choose Google and start a fresh session. Verify the configured Achird output,
   then end and switch back to Sarvam. Compare server `voice_latency` events
   using opaque session IDs; no transcripts, audio or credentials should appear.
6. Close the tab, stop the worker, or simulate a connection failure. Verify local
   media stops and the server closes the session. Transport failure shows Error;
   an ordinary peer shutdown shows Disconnected. Detailed provider errors stay
   server-side and sanitized.

The local Web Audio graph samples existing microphone/remote media streams for
amplitude meters. Its extra destination gain is zero to avoid microphone echo
or duplicate assistant playback; audible playback still uses the audio element.
No analyser data leaves the browser and no extra API request is made. End and
failure disconnect nodes, cancel animation and close the audio context. Unsupported
Web Audio falls back to the same conversation controls without meters.

## Offline validation

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
node --check apps/voice-worker/src/techfi_voice/static/client.js
node --check tests/test_playground_client.cjs
node --test tests/test_playground_client.cjs
```

Python route tests mock transports, connections and session execution; Node's
built-in test runner uses a fake DOM, microphone, WebRTC peer and fetch. They
exercise allowlist rejection, server-owned settings, credential isolation,
concurrent settings separation, selector locking, cancellation and cleanup.
They do not establish visual quality, real browser compatibility, accessibility
compliance or live audio behavior. The optional socket integration test remains
excluded. Google credential validity and recognition/synthesis quality remain
unverified by this offline-only change.

Validation on 18 October 2026: **94 Python tests passed**, one optional socket
integration test deselected; **4 mocked Node tests passed**; Ruff lint/format
and both JavaScript syntax checks passed. Eight Python warning occurrences
originate upstream. No paid provider calls, live sessions or visual browser
acceptance checks were run.


For the new experimental combinations, follow the additional acceptance steps
in [the Sarvam experimental guide](sarvam-experimental.md). Earlier validation
counts above describe the original playground milestone, not the expanded suite.

The [Soft Collection demo](soft-collection.md) adds Agent Type, grammatical persona,
validated fictional case fields and a temporary structured-outcome panel. Its
server-owned policy defaults disabled; all configuration locks per session.
The provider choices now also resolve approved male/female server voice presets.
