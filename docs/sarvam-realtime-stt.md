# Sarvam Realtime STT

The optional `sarvam-realtime` selector uses Pipecat 1.12.0's supported
`SarvamRealtimeSTTService` with `Settings(model="saaras:v4", ...)`. The existing
lockfile already supplies this service; no dependency upgrade is required.
OpenAI LLM (`gpt-4.1-mini`) and Sarvam TTS (`bulbul:v3`) are unchanged.
The older `sarvam` selector still uses the earlier Sarvam STT adapter.

## Switching providers manually

Stop the worker and finish active sessions before changing configuration. From
the repository root, with your existing ignored `.env`, validate realtime settings:

```sh
TECHFI_STT_PROVIDER=sarvam-realtime TECHFI_STT_MODEL=saaras:v4 TECHFI_STT_MODE=codemix TECHFI_STT_STREAM_TYPE=balanced uv run --no-sync techfi-voice --check-config
```

To start a future manual audio test, use the same command without `--check-config`:

```sh
TECHFI_STT_PROVIDER=sarvam-realtime TECHFI_STT_MODEL=saaras:v4 TECHFI_STT_MODE=codemix TECHFI_STT_STREAM_TYPE=balanced uv run --no-sync techfi-voice
```

`TECHFI_LANGUAGE=hi-IN` is the default; `hi` maps to `hi-IN`. Use `codemix`
for Hindi/English mixed speech; `transcribe`, `verbatim`, and `translit` are
also accepted. There is no invented `hinglish` language code. Keep
`TECHFI_LLM_MODEL=gpt-4.1-mini` and `TECHFI_TTS_MODEL=bulbul:v3` in your
existing configuration. These commands override STT selection only.

To return to Whisper, stop the worker and validate:

```sh
TECHFI_STT_PROVIDER=openai TECHFI_STT_MODEL=whisper-1 TECHFI_STT_MODE=transcribe uv run --no-sync techfi-voice --check-config
```

Then restart with:

```sh
TECHFI_STT_PROVIDER=openai TECHFI_STT_MODEL=whisper-1 TECHFI_STT_MODE=transcribe uv run --no-sync techfi-voice
```

Alternatively persist the same nonsecret values in your local `.env` and run
the usual startup command. `SARVAM_API_KEY` supplies realtime STT and Sarvam TTS;
`OPENAI_API_KEY` supplies OpenAI LLM and Whisper. An explicit
`TECHFI_STT_API_KEY` takes precedence and must match the selected provider.
Never paste keys into commands or logs. Preflight validates configuration only,
without provider connections or credential checks. The application does not
modify `.env`, and Whisper remains the default selector.

## Streaming, endpointing and session behavior

Each session owns one persistent WebSocket, its audio buffer and conversation
history. Input is mono 16 kHz signed 16-bit little-endian PCM (`linear16`).
Pipecat sends 50 ms chunks while speech is happening. Balanced streaming is
the default; `TECHFI_STT_STREAM_TYPE=fast` is available for later evaluation.
Manual endpointing sends local VAD speech-start/speech-end events and flushes
the remaining audio before speech-end. No additional flush message or competing
server endpoint detector is introduced. Existing local VAD and turn-silence
thresholds remain unchanged.

Only nonempty finalized transcripts reach opt-out handling and conversation
aggregation. Partials are discarded at this boundary; empty-turn LLM recovery
is disabled for this path. Local VAD interruptions still pass through.

Pipecat disables automatic reconnection for this realtime service. A connection
error, unusable socket or provider progress timeout ends the current session;
audio is not replayed, history is not merged and Whisper is not selected
automatically. Start a new session after recovery or a manual provider switch.
This favors transcript consistency over transparent reconnects.

The existing setup/start deadline (`TECHFI_CONNECTION_TIMEOUT_SECONDS`, default
20), stage watchdog (`TECHFI_PROVIDER_TIMEOUT_SECONDS`, default 20) and teardown
deadline (`TECHFI_CLEANUP_TIMEOUT_SECONDS`, default 10) remain active.
`TECHFI_STT_KEEPALIVE_SECONDS` defaults to 10 (1–60); Pipecat sends WebSocket
keepalive messages rather than synthetic customer audio. Stop, failure and
cancellation close the socket and clear session history.

## Diagnostics and validation

Safe events `stt_connected`, `stt_disconnected`, `stt_connection_failed`,
`stt_timeout`, and `stt_finalized` carry an opaque UUID `session_id`.
`stt_finalized` additionally reports `partial_count` since the preceding final
or speech/interrupt boundary. These are delivery diagnostics, not recognition
accuracy scores. Logs contain no transcripts, provider request IDs, audio or
raw exception messages. Existing [latency metrics](latency.md) remain enabled;
realtime STT completion requires a finalized transcript.

Offline tests mock provider connections and cover construction, 16 kHz chunking,
VAD event ordering, partial/final handling, LLM gating, timeouts, connection
failure and cleanup. Run:

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
```

No live Sarvam session, paid API request, recognition-quality evaluation or
microphone-to-speaker test was performed for this change. For a separately
authorized manual test, compare Hindi/Hinglish utterances, numbers and names,
pause behavior, barge-in and disconnect recovery using synthetic identities.
Compare final-transcript and speech-end-to-first-audio p50/p95 against Whisper.
Streaming can remove post-speech upload/inference work, but balanced finalization,
unchanged endpoint delays, LLM and TTS still contribute. Sub-two-second response
latency is a target, not a validated result.

API references: [Pipecat Sarvam STT](https://docs.pipecat.ai/api-reference/server/services/stt/sarvam)
and [Sarvam realtime streaming](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming).
The installed pinned implementation was checked for constructor settings,
manual endpoint events and final-frame semantics; recheck these before upgrades.
