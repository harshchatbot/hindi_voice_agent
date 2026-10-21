# Live timing review — 19 October 2026

This review uses the user's supplied content-free JSON trace. No provider API
request or live session was executed during the investigation. Previously
uncommitted changes remain uncommitted.

## What the trace establishes

The six responding speech windows reproduce the reported averages: **4,422 ms**
speech-end to first audio, **2,554 ms** STT completion including VAD silence,
**1,349 ms** LLM first text and **456 ms** TTS first audio.

| Window | STT handoff → first final (ms) | First final → strategy readiness (ms) | Speech end → first audio (ms) |
| --- | ---: | ---: | ---: |
| 1 | 2,811 | 1.6 | 6,733 |
| 2 | 3,357 | 1.1 | 5,861 |
| 3 | 3,247 | 0.4 | 5,034 |
| 5 | 801 | 4.1 | 2,754 |
| 6 | 1,244 | 0.8 | 2,548 |
| 8 | 1,457 | 3.0 | 3,602 |

The 400 ms VAD silence explains handoff timing, but not the remaining seconds.
The policy floor has already expired when these first finals arrive. Lowering
`TECHFI_TURN_SILENCE_SECONDS` would not remove those observed STT waits.

### The stack was not identified correctly

The securely inspected local configuration resolves to **`openai` / `whisper-1`**,
with OpenAI GPT-4.1 Mini. The process-wide TTS configuration is Google; the
playground's independent default Sarvam selection can override TTS for a session.
Neither configuration values nor credentials were changed.

The supplied trace also has the fingerprint of the unfenced legacy/segmented
STT path: repeated `stt_final` stage records in one timing window, no
`stt_connected`, no `stt_finalized`, and continuation after overlapping unfinished
endpoints. The current realtime gate allows only one accepted final per endpoint
and would fail closed on those overlaps. An earlier code version or different
shell overrides cannot be ruled out retrospectively, because the trace did not
record the selected stack. These logs do **not** establish a Sarvam service-side
finalization regression.

The `stt_endpoint_handoff` event previously meant only “VAD stop entered the STT
processor.” It is not evidence of a Sarvam `speech_end` message being sent.
For Whisper it means batch segmentation/queuing, not a WebSocket endpoint.

### Extra finals and resumed speech

Window 4 was superseded **1,180 ms** after handoff; window 7 after **359 ms**.
Both had no accepted transcript or LLM request at supersession. This indicates
new VAD speech activity before the old result arrived; it does not establish
provider failure or distinguish a within-sentence pause from a new utterance.

Later extra finals occur in window 5 **3,470 ms after first audio**, and window 8
**158 ms after first audio**. Pinned Pipecat's segmented STT queues VAD-delimited
requests and processes them serially. Thus an old window's result can arrive
while a later window is current, followed by the later queued result. Queue
backlog can contribute to elapsed time before transcription is available.
This is consistent with the 4→5 and 7→8 pattern, but timing alone cannot prove
whether particular texts were duplicates or different segments. No transcript
logging, text-based deduplication or guessed utterance IDs are added.

The earlier non-realtime timing path assigned completed segments to the mutable
current window and re-emitted `stt_final`. Its phase deduplication also meant that
one logged `llm_request` was not proof of only one actual inference. Late text
could reach the aggregator or opt-out policy before this fix. We cannot certify
that the historical conversation context was uncontaminated from these logs.

The earlier `turn_cancelled` record was also emitted when moving past a window
that had already produced a reply. It did not distinguish interrupted generation,
normal timing-window closure or pipeline shutdown.

## Focused code changes

1. Worker startup/preflight emits `worker_configuration`; each built session
   emits `session_configuration` with an opaque session UUID. Only fixed,
   allowlisted profiles, stream type, endpointing and fence status are serialized.
   No arbitrary model strings, file paths, keys, SDP or customer data are logged.
   Session diagnostics reflect the actual playground TTS selection; worker
   diagnostics reflect process configuration.
2. The existing bounded transcript fence now also protects native
   `SegmentedSTTService` instances such as Whisper, before opt-out handling and
   context aggregation. A final with no pending boundary, or new speech while
   an older boundary is unresolved, ends the session safely. No uncertain text
   is accepted, replayed into another window or promoted to an opt-out decision.
   The established Sarvam realtime fence remains active.
3. `stt_final_received` counts finalized frames at provider output using only a
   per-session counter. It intentionally has no inferred turn owner.
   `stt_final` / `stt_finalized` continue to mean gate-accepted input for fenced
   sessions. Boundary errors carry a fixed `boundary_reason`.
4. Sarvam's pinned native `_send_json` hook records `stt_endpoint_sent` only when
   the actual `speech_end` socket send returns successfully. Tail flushing,
   50 ms audio chunks and native protocol behavior are unchanged. A failed send
   reports an internal pipeline error rather than silently waiting for a final.
   This is local socket-send completion, not a Sarvam acknowledgment. The small
   adapter is specific to pinned Pipecat 1.12.0 and covered by offline tests.
5. `llm_response_end` and typed terminal reasons distinguish normal
   `timing_window_closed` from `turn_cancelled`. Reasons include speech resuming
   before final, speech during LLM generation, speech while the server bot is
   active, explicit interruption frames, session end and pipeline cancellation.
   Server speaking state is not proof of browser audibility. Historical reasons
   cannot be reconstructed perfectly from the old ambiguous event.

No VAD threshold, policy floor, final deadline, default stream profile, LLM,
TTS voice, provider credentials or local `.env` was changed in this review.
Payment/identity restrictions and opt-out matching remain unchanged. Ambiguous
late input is fenced before either business/conversation processing or opt-out.
Failing closed can end a session during a long STT stall followed by resumed
speech; this is an explicit accuracy/availability tradeoff, not graceful replay.

## Smallest useful manual comparison

First establish the intended stack without opening a provider connection:

```sh
TECHFI_STT_PROVIDER=sarvam-realtime TECHFI_STT_MODEL=saaras:v4 TECHFI_LLM_PROVIDER=openai TECHFI_LLM_MODEL=gpt-4.1-mini TECHFI_TTS_PROVIDER=sarvam TECHFI_TTS_MODEL=bulbul:v3 TECHFI_TTS_VOICE_ID=shubh TECHFI_STT_STREAM_TYPE=balanced uv run --no-sync techfi-voice --check-config
```

Verify `stt_profile="sarvam_realtime_v4"`,
`llm_profile="openai_gpt_4_1_mini"`, `tts_profile="sarvam_bulbul_v3"`,
`endpointing="manual"` and `stream_type="balanced"`. Explicit role-key overrides,
if configured, must belong to the selected provider. Preflight does not verify
remote credentials. This command is documented for the user, not executed here.

For a separately authorized live baseline, run that same command without
`--check-config`, keep the playground's Sarvam voice selected, and verify the
per-session profile plus `transcript_fence=true`. Keep the existing 400/600 ms
silence settings. Compare:

- Handoff → endpoint sent: local tail flush/socket-send delay.
- Endpoint sent → accepted final: remaining delivery/network/provider latency,
  not isolated inference time.
- Raw finalized-frame count versus accepted-final count and boundary reasons.
- Cancellation reasons and actual number of mocked/observed inference dispatches.

Only after confirming Sarvam is active, repeat with
`TECHFI_STT_STREAM_TYPE=fast` as the **only** setting changed. Both profiles retain
16 kHz PCM, the same persistent socket and manual endpointing. Google TTS remains
selectable, but keep Sarvam selected for this comparison.

Sarvam's official guide describes `fast` primarily as a **partial-transcript**
latency/accuracy tradeoff. Pipecat describes it as server flush cadence. Neither
establishes an accuracy-preserving reduction in final latency for this workload.
Partials remain diagnostic and cannot trigger the LLM or financial/identity/
opt-out decisions. Do not change `silence_duration_ms` in manual mode: it only
controls server VAD. Do not add `flush` after `speech_end`: it is another
force-finalization primitive and can obscure or duplicate boundary behavior.
Do not reconnect per utterance or switch providers automatically.

Before accepting `fast`, compare Hindi/Hinglish dates, amounts, names, paused
numbers, self-corrections, interruptions and opt-outs with synthetic identities.
No live latency improvement or recognition-quality equivalence is claimed.

The fence proves rejection of unresolved overlapping endpoints and finals outside
a pending boundary. It cannot identify an unsolicited duplicate from an already
accepted earlier utterance if that duplicate arrives during a later, otherwise
valid pending boundary: the current provider frames have no verified utterance
identity. No heuristic text matching or assumption about optional timestamp
origins is introduced. Sarvam's one-final-per-manual-utterance behavior and any
offset/identifier semantics still need validation before claiming universal
cross-turn duplicate detection. The supplied trace cannot establish that Sarvam
violates this contract, because it is consistent with the unfenced batch path.

Offline validation: **117 tests passed**, one socket integration test deselected;
Ruff lint/format passed. Thirteen upstream deprecation warnings remain. Regression
tests cover late finals after first audio, unresolved overlaps for realtime and
segmented STT, actual LLM invocation/history/opt-out isolation in those cases,
normal versus interrupted timing windows, profile redaction, and successful or
failed mocked endpoint sends. No keys, configuration files, thresholds or stream
defaults were modified; no commit or push was made.

References: [Sarvam realtime streaming](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming),
[Pipecat Sarvam STT](https://docs.pipecat.ai/api-reference/server/services/stt/sarvam),
and the installed 1.12.0 `SegmentedSTTService` / Sarvam implementation.
