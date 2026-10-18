# Voice latency instrumentation

The worker reports content-free `voice_latency` JSON events automatically. No extra provider request, recording, audio export, transcript forwarding or remote metrics backend is added. `worker_started`, `worker_completed` and `worker_failed` from the session runtime include the same opaque `session_id`.

A synthetic example:

```json
{"event":"voice_latency","level":"INFO","session_id":"00000000-0000-4000-8000-000000000001","turn_index":1,"metric":"llm_first_token_ms","duration_ms":200.0}
```

Group records by `session_id` and `turn_index`. `turn_index` counts VAD speech-end boundaries, not business outcomes; resumed speech can create another boundary. Session IDs are generated UUIDs, never customer/phone/account IDs. The formatter accepts only typed UUIDs, fixed metric names, positive boundary indices and finite nonnegative durations. It drops arbitrary extras and exception payloads. Model/provider names, TTS context IDs, raw timestamps, audio, user/assistant text, and credentials are not serialized.

## Definitions

| Metric | Measured interval | What it tells you |
| --- | --- | --- |
| `stt_completion_ms` | Estimated physical speech end → last completed transcription frame used before the LLM request | Endpoint confirmation, upload/network and transcription delay together |
| `llm_first_token_ms` | LLMFullResponseStartFrame from the LLM → its first LLMTextFrame | Time to first answer text; not time to full answer and not reasoning-token TTFB |
| `tts_first_audio_ms` | Supported TTS `on_tts_request` callback → first matching TTSAudioRawFrame | Time to first generated audio for the first synthesis request in the measured response |
| `speech_end_to_first_audio_ms` | Estimated physical speech end → first matching generated audio | Server response generation latency, including turn detection and text aggregation gaps |
| `speech_end_to_output_start_ms` | Estimated physical speech end → output transport BotStartedSpeakingFrame | Server output-start proxy, including queue/transport handling observed by the worker |

All intervals use a shared Pipecat `SystemClock` (`time.monotonic_ns`). Frame timing comes from the supported `BaseObserver` / `FramePushed.timestamp` interface at the time the frame is pushed, not when the asynchronous observer processes it. TTS request callbacks sample that same clock. TTS callbacks are scheduled by Pipecat, so their timestamp can include callback scheduling error. Frame clocks and callbacks share the same origin; no wall-clock timestamp is subtracted from a monotonic time.

Pipecat 1.12.0's native TTFB metrics and aggregate UserBotLatencyObserver use wall-clock timestamps. This implementation instead uses supported frame/events to meet the monotonic-clock requirement. Native usage metrics, tracing, RTVI forwarding and dependency logs remain disabled. No parallel pipeline or SDK calls are introduced.

VAD declares speech ended after a configured silence window. We subtract `VADUserStoppedSpeakingFrame.stop_secs` from its monotonic timestamp to estimate the physical end of speech, ignoring its wall-clock `timestamp` field. This includes the VAD silence window in the reported speech-to-response latency. It is an estimate: input buffering, audio-device delay and VAD scheduling can affect the actual boundary.

InterimTranscriptionFrame is excluded. The `sarvam-realtime` path additionally requires `TranscriptionFrame.finalized=True`; partial and nonfinal frames cannot complete STT timing or cancel its watchdog. Other adapters retain the existing completed-segment frame-type behavior for compatibility. For providers that emit multiple completed segments, the last segment seen before LLM start is used. STT timing is emitted when that request starts, or on interruption/end/cleanup if a completed segment exists. It excludes any transcription arriving after LLM inference has already started.

## Audible response limitations

**Output start is not proof of audibility at the browser.** Pipecat's BotStartedSpeakingFrame can precede actual speaker playback and TTS audio can contain leading silence. WebRTC delivery, jitter buffering, autoplay policy, output-device buffering and acoustic playback are not measured here. The generated-audio and output-start metrics are useful server proxies, not a claimed end-to-end audible latency. A true first-audible metric requires client playback/audio analysis and clock correlation, which this focused change does not add.

Greeting, fixed opt-out speech, cancelled inference, empty answers, tool-only responses and missing/unsupported provider events may omit some or all latency stages. Missing values are not logged as zero. Once a response is interrupted, stale timing is discarded; matching TTS context IDs are used internally to avoid pairing unrelated audio with a new request. Only the first token/audio/output interval per boundary is emitted. TTS request metadata is bounded to 32 pending contexts per session and cleared at teardown. No transcript/audio is retained by the timing state.

## Identifying bottlenecks

- High `stt_completion_ms`: check VAD stop duration, network/upload time and transcription performance. Whisper is utterance-based; it uploads after VAD segmentation. This metric is not pure provider processing time.
- High `llm_first_token_ms`: compare model latency and prompt/history size using synthetic data. It includes service request/stream delay and time before answer text; it says nothing about full-response generation time.
- High `tts_first_audio_ms`: check synthesis model/voice startup, provider connection and text size. It starts after text aggregation has produced a TTS request.
- Fast component metrics but high `speech_end_to_first_audio_ms`: examine turn-silence policy and the wait for enough LLM text to synthesize a sentence. Those waits are not included in the LLM/TTS component intervals.
- A large gap from generated audio to output start suggests server output buffering/scheduling. Slow perceived playback with normal server metrics points toward the browser/network/device side and needs client measurements.

Do not blindly sum component metrics: pipeline stages overlap, STT includes endpointing, and text aggregation/turn policy introduce additional gaps. Compare p50/p95 of each metric over representative completed turns. Compare like-for-like configurations, and do not treat omitted samples from interrupted or failed responses as successful zero-latency calls. The logs intentionally contain no user identity or configuration labels; manage any configuration grouping outside these customer sessions.

## Prompt migration and validation

The unchanged `conversational_prompt(settings.system_prompt)` is now passed as `Settings(system_instruction=...)` to both OpenAI and Groq LLM integrations. LLMContext starts empty and contains conversation history only. The provider adapter still prepends the same system instruction to the actual request, preserving the loan-reminder/identity/disclosure/opt-out instructions and removing the deprecated initial-context system message.

Offline regression tests use synthetic timestamps and payloads to verify exact intervals, clock independence, stage deduplication, cancellation/session isolation, formatter rejection of unsafe values, both provider settings and the final provider-request prompt. No paid API call, live conversation or socket integration test is needed. Run:

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -q
```
