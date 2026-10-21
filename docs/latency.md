# Voice latency investigation and instrumentation

This change is validated offline against **Pipecat 1.12.0**. No provider request
or live audio test was performed. No latency improvement is claimed until the
new traces have been compared during manual testing.

## Confirmed event flow and waits

Local Silero VAD broadcasts a speech stop after `TECHFI_VAD_STOP_SECONDS`
(default **0.4 seconds**) of silence. The upstream copy reaches Sarvam Realtime
STT. Its supported manual-endpointing implementation flushes the remaining
PCM audio before sending `speech_end`. It does **not** wait for the aggregator's
turn-stop decision, and no extra provider `flush` event is needed. Sending
`speech_end` only after a final-dependent turn stop would create a circular wait.

`SpeechTimeoutUserTurnStopStrategy` starts two independent waits at VAD stop:
its policy floor (`TECHFI_TURN_SILENCE_SECONDS`, default **0.6 seconds**) and an
STT metadata safety timer. A finalized transcript short-circuits the STT timer,
but not the policy floor. Thus, approximately:

```text
LLM readiness = VAD stop + max(policy floor, time until accepted STT final)
```

The 0.4-second VAD silence is before those waits; the 0.6-second policy and STT
finalization are **overlapping**, not sequential. If STT is slower than the policy,
removing the policy won't fix the STT outlier. If STT is fast, the policy can
add a measurable gap, but it deliberately protects pauses within an utterance.
No evidence justifies declaring all of that pause protection redundant.

`FinalTranscriptGate` forwards a valid final immediately; it adds no fixed delay.
Partials are excluded from opt-out detection, history and LLM execution. There
is no speculative response, inferred payment information or filler speech.

OpenAI already sends `stream=True` and emits incremental `LLMTextFrame` objects.
TTS uses Pipecat's supported sentence aggregator. A completed first sentence can
start synthesis before `LLMFullResponseEndFrame`; an unpunctuated first sentence
waits for a boundary or the final flush. Token-by-token synthesis was not enabled
because tiny independent requests can harm prosody and increase request count.

Offline native-strategy tests demonstrate both final-before-policy and
final-after-policy ordering, including cancellation of the STT safety timer.
Mocked native OpenAI streaming and native sentence-aggregation tests establish
incremental delivery without waiting for the whole reply.

## Confirmed fixes

- The prior observer could count upstream/downstream VAD instances separately
  because the user aggregator emits unpaired directional frames. Instrumentation
  now observes the canonical aggregator boundary and deduplicates its state.
- Late provider responses were associated with mutable current-turn state. LLM
  input metadata and task-local `ContextVar` ownership now follow the actual
  request through incremental output. TTS ownership travels through its public
  `process_frame`/text-transform/`run_tts` extension points. Context IDs remain
  internal; delayed audio never gets relabeled as a new turn.
- Realtime finals now have a dedicated bounded deadline, default **5 seconds**
  from the VAD endpoint handoff. The gate owns it; the general STT watchdog is
  disabled only for this path. Partials don't rearm it. Empty/nonfinal results
  don't satisfy it. Timeout ends the session safely, fences late finals and
  closes its timer during teardown.
- Pipecat's separate no-progress turn fallback is configured later than that
  deadline (`final timeout + policy floor + 1 second`), so it cannot flush an
  empty turn and accept a late final into a subsequent turn first.
- If speech resumes while a manual endpoint is still unresolved, the gate keeps
  the existing Pipecat user turn open and quarantines finalized segment text.
  Nothing reaches opt-out, collection rules, history or the LLM until every
  endpoint created by that pause/resume sequence has returned a distinct final
  and VAD is silent again. Those finals are then released as one finalized user
  transcript. The original final deadline is not extended while an older
  endpoint remains unresolved. Finals outside an endpoint, duplicate finals,
  deadline expiry and cancellation still fail closed or discard quarantined
  text. This supports natural pauses without weakening downstream transcript
  safety; provider frames still expose no verified utterance ID, so unexpected
  non-identical cross-turn duplicates remain a provider-contract limitation.

There was no proven redundant serial STT wait to delete. Silence thresholds
remain unchanged pending manual accuracy/latency evaluation. The source defaults
are now Sarvam Realtime Saaras V4, OpenAI GPT-4.1 Mini and Sarvam Bulbul V3/Shubh.
Google remains selectable. Existing `.env` values still take precedence and were
not modified.

## Privacy-safe timestamps and durations

`voice_turn_timing` contains only a fixed stage, opaque session UUID, numeric
`turn_index`, and `at_ms`: elapsed milliseconds on the session's monotonic
Pipecat clock. Once speech end is known, `since_speech_end_ms` also reports
the elapsed duration to that stage. It is not wall time or a browser clock. No transcripts, raw audio,
credentials, provider payloads, request/context IDs or user identifiers are logged.
Typed logging accepts only whitelisted stages and finite nonnegative numbers.

Synthetic example:

```json
{"event":"voice_turn_timing","level":"INFO","session_id":"00000000-0000-4000-8000-000000000001","turn_index":1,"stage":"vad_stop","at_ms":2400.0}
```

| Stage | Timestamp meaning |
| --- | --- |
| `estimated_speech_end` | VAD observation minus the frame's `stop_secs`; physical-end estimate |
| `vad_stop` | Canonical local VAD stop notification |
| `stt_endpoint_handoff` | VAD stop reaches STT processor entry; before native tail flush / socket send |
| `stt_first_interim` | First interim available at the gate; diagnostic only |
| `stt_final` | Nonempty final accepted by the gate |
| `turn_strategy_complete` | Stop strategy signals inference readiness, before aggregator context dispatch |
| `llm_request` | Context is dispatched to the LLM processor |
| `llm_start` | Native LLM response-start frame, before remote completion work |
| `llm_first_text` | First streamed LLM answer text |
| `tts_first_text_delivered` | First LLM text actually processed by TTS, before sentence aggregation |
| `tts_first_text_chunk` | First aggregated chunk passes TTS's text transform, unchanged |
| `tts_request` | Entry into the supported `run_tts` synthesis operation |
| `tts_first_audio` | First correlated audio frame emitted by TTS |
| `audio_transport_handoff` | Correlated audio enters output transport processing, before transport buffering |
| `turn_cancelled` | Timing window superseded/invalidated; not a persisted business call outcome |
| `stt_final_timeout` | Final transcript deadline expired |

A `turn_index` identifies a VAD speech segment / timing window. A pause followed
by resumed speech may create another index even when the conversation aggregator
combines accepted text into one user message. The estimate is logged retrospectively
at VAD stop. Interim availability may precede that estimate; compute its signed
difference rather than forcing it to zero. Multiple sessions have independent
clock origins and bounded state (32 timing windows / TTS contexts). Do not
subtract `at_ms` values from different session IDs or turn indices.

Existing `voice_latency` names are retained for STT completion, LLM first text,
TTS first audio and speech-end-to-first-audio. They still emit finite monotonic
intervals. TTS timing now starts synchronously at synthesis entry rather than
at a separately scheduled event callback. This is more precise but should be
noted when comparing earlier measurements.

The previous `speech_end_to_output_start_ms` is not guessed for bound session
traces: Pipecat's output rechunking discards context IDs, so a later
`BotStartedSpeakingFrame` cannot always be assigned safely after interruptions.
Use the new correlated `audio_transport_handoff` boundary. Neither timestamp
measures a successful WebRTC packet transmission or browser audibility. Device
latency, network/jitter buffers, autoplay and leading silence remain unmeasured.

Only accepted/attributable phases are emitted. Greeting, fixed opt-out speech,
missing stages, interrupted requests and expired/ambiguous finals may omit later
measurements; omissions are not zero-latency successes. Earlier completed samples
may remain in the log when their timing window is later superseded. Stage
ownership is explicitly implemented for OpenAI LLM with Sarvam/Google TTS;
other retained adapters can omit unsupported stages rather than infer ownership.

## Locating the bottleneck

Compute these differences within each `(session_id, turn_index)`:

| Difference | Interpretation |
| --- | --- |
| VAD stop − estimated speech end | Configured endpoint silence plus scheduling effects |
| Endpoint handoff − VAD stop | Pipeline scheduling before Sarvam receives its boundary |
| STT final − endpoint handoff | Tail flush/send, network and provider finalization together; not pure inference time |
| Turn strategy complete − max(VAD stop, STT final) | Remaining policy floor / local strategy scheduling |
| LLM start − turn strategy complete | Aggregator / dispatch scheduling |
| LLM first text − LLM start | Remote request and answer-text latency |
| TTS first text delivered − LLM first text | Downstream queue/backpressure |
| TTS first text chunk − TTS first text delivered | First-sentence aggregation delay |
| TTS request − TTS first text chunk | Local TTS preparation / queueing |
| TTS first audio − TTS request | Synthesis/network/first emitted chunk delay |
| Audio transport handoff − TTS first audio | Pipeline delivery to transport |
| TTS first audio − estimated speech end | Total server-side speech-end-to-first-audio latency |

Do not sum independently averaged stage metrics; overlapping waits and omitted
samples make that misleading. Compare p50/p95 across representative synthetic
Hindi/Hinglish turns and count failed/ambiguous/expired turns separately.

## Configuration and manual comparison

No `.env` was changed. Relevant validated controls:

| Variable | Default / range |
| --- | --- |
| `TECHFI_VAD_START_SECONDS` | 0.2; 0.05–1 |
| `TECHFI_VAD_STOP_SECONDS` | 0.4; 0.1–2 |
| `TECHFI_TURN_SILENCE_SECONDS` | 0.6; 0–3 |
| `TECHFI_STT_FINAL_TIMEOUT_SECONDS` | 5; 0.5–30 |
| `TECHFI_PROVIDER_TIMEOUT_SECONDS` | 20; unchanged LLM/TTS progress protection |
| `TECHFI_STT_STREAM_TYPE` | `balanced`; `fast` remains an explicit option |

For your manual test, first keep the old 0.4/0.6 silence thresholds and compare
traces. If a substantial gap consistently remains between final and strategy
completion, evaluate a smaller post-VAD policy such as `0.3` explicitly:

```sh
TECHFI_TURN_SILENCE_SECONDS=0.3 uv run --no-sync techfi-voice
```

This is an opt-in experiment, not a proven accuracy-preserving default. Test
within-sentence pauses, amounts/dates/names, Hindi and mixed English, rapid
barge-in, missing/late finals and opt-out. Do not lower VAD and policy together
when trying to isolate the cause. Use the playground's Sarvam or Google choice
between sessions. Stop and restart to revert. A separately authorized manual
session can incur provider charges; none was launched automatically here.

Offline checks:

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
node --check apps/voice-worker/src/techfi_voice/static/client.js
node --test tests/test_playground_client.cjs
```

Source references: [Pipecat turn strategies](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies.md),
[Sarvam STT integration](https://docs.pipecat.ai/api-reference/server/services/stt/sarvam),
and installed pinned `speech_timeout_user_turn_stop_strategy.py`,
`llm_response_universal.py`, Sarvam/OpenAI/TTS services and `base_output.py`.
These implementation boundaries were checked directly rather than inferred
from aggregate manual measurements. No changes were committed or pushed.

Validation on 18 October 2026: **106 Python tests passed**, one socket test
deselected; **4 mocked Node tests passed**. Ruff lint/format, JavaScript syntax
and diff whitespace checks passed. Nine warnings originate upstream. The
manual microphone/provider latency comparison remains unperformed.

## Follow-up: supplied live trace

The [19 October trace review](stt-live-review-2026-10-19.md) identifies a local
Whisper override, delayed-segment risks and misleading cancellation labels.
The fence now also protects native segmented STT; configuration profiles, raw
final-frame counters, successful Sarvam endpoint sends and typed closure reasons
make future runs distinguishable without logging content.
