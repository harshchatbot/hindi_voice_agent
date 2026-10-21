# Experimental Sarvam conversation LLM and Bulbul v4 Flash

Reviewed 19 October 2026. These are selectable experiments in the existing Pipecat
worker, not a migration. GPT-4.1 Mini and Bulbul v3/Shubh remain defaults; Google
Chirp 3 HD/Achird remains selectable. The STT factory, finalized-transcript fence,
prompt and session lifecycle are unchanged. Keep STT on Sarvam Realtime Saaras v4.

## Verified contracts and adapter limitations

The [collection-agent cookbook](https://docs.sarvam.ai/api/cookbook/example-voice-agents/collection-agent)
is a LiveKit example and lists v3 short speaker names. Its architecture and voice
IDs must not be copied into our Pipecat/v4 integration.

- [Sarvam-105B Conversations](https://docs.sarvam.ai/api/getting-started/models/sarvam-105b#conversational-variant)
  supports OpenAI-compatible streamed Chat Completions at **V1 only**, with a
  **32K-token** context window. The adapter reuses our timed, bounded OpenAI client
  with the Sarvam key and `https://api.sarvam.ai/v1`, preserving incremental text,
  `system_instruction`, per-session context and cancellation. Authentication uses
  the documented Bearer compatibility mode. It explicitly sends
  `reasoning_effort: null` to disable default thinking and sends only reply content
  to TTS. It omits OpenAI-only parameters rather than assuming compatibility.
- [Bulbul v4 guidance](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/best-practices)
  lists **`shubh_hi_customer`** as a supported male Hindi persona. This is our
  experimental default, not the v3 ID `shubh`. The limited server allowlist also
  permits `shubh_hi_ecomm` and `shubh_enhi_banking` for later manual comparisons.
  v3 short IDs are rejected before client creation. For mixed speech, the provider
  recommends each language's native script; Romanized Hindi pronunciation is not
  guaranteed. No automatic text rewriting has been added.
- [The v4 WebSocket contract](https://docs.sarvam.ai/api-reference/text-to-speech/stream)
  supports persistent sockets, JSON config/text/flush/ping, base64 audio and
  `send_completion_event=true` final events. We request 24 kHz mono LINEAR16 PCM,
  explicit v4 model/persona, `language_code=hi-IN` and preprocessing. We do not
  carry over v3 temperature, buffering or unsupported defaults.
- **Pinned Pipecat 1.12.0's native Sarvam TTS constructor rejects v4.** The small
  version-scoped shim in `integrations/sarvam_experimental.py` initializes
  `InterruptibleTTSService` directly and reuses Pipecat's Sarvam socket methods.
  It replaces the constructor/config only; it does not patch global model/voice
  registries or pretend v4 is v3. This is an application compatibility adapter,
  not native upstream v4 support. Revalidate it against installed source and tests
  on every Pipecat upgrade. Existing lockfile/dependency versions are unchanged:
  Pipecat 1.12.0, OpenAI SDK 3.35.1, Sarvam SDK 0.1.28; the new v4 wire configuration
  bypasses the older Sarvam SDK's generated voice catalog.

The [streaming guide](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/streaming-api/web-socket)
has no server cancel/clear command. Pipecat clears local playback/context and closes
and reopens TTS on interruption (including before first audio). Normal text chunks
share the session socket; unexpected socket errors fail through existing session
handling, without automatic provider switching or transcript replay. Native
Pipecat ping, flush, completion, teardown and our timing mixin remain in use.
LLM HTTP reads use the existing provider timeout with retries disabled; session
watchdogs and existing worker deadlines bound stalled output and cleanup.

This integration is **text dialogue only**. Tool calling, structured JSON output,
reasoning traces, Responses/V2, multimodal input, session-affinity/prompt caching,
v4 prosody controls, cloned voices, pronunciation dictionaries and word timestamps
are not exposed or validated here. v4 temperature is ignored by the API and is not
sent. No automatic context trimming is implemented: keep conversations within the
32K window. Account access, quotas, current voice availability, PCM decoding on the
live service, perceived quality and latency still require manual evaluation.
No under-two-second latency guarantee is made.

## Configuration

Keep real credentials in the existing ignored `.env` or injected environment;
never copy `.env.example` over configured credentials. Both `OPENAI_API_KEY` and
`SARVAM_API_KEY` should be available to compare providers. The latter can serve
STT, experimental LLM and Sarvam TTS. Explicit `TECHFI_*_API_KEY` role keys still
win for their configured provider. Switching an LLM uses that provider's conventional
key, never the other provider's role key. The v3/v4 TTS variants share a Sarvam
account boundary and may reuse its configured TTS role key. Google continues to
use ADC as documented in [Google setup](google-tts.md).

| Server setting | Default |
| --- | --- |
| `TECHFI_PLAYGROUND_OPENAI_MODEL` | `gpt-4.1-mini` |
| `TECHFI_PLAYGROUND_SARVAM_LLM_MODEL` | `sarvam-105b-conversations` |
| `TECHFI_PLAYGROUND_SARVAM_MODEL` | `bulbul:v3` |
| `TECHFI_PLAYGROUND_SARVAM_VOICE_ID` | `shubh` |
| `TECHFI_PLAYGROUND_SARVAM_V4_MODEL` | `bulbul:v4-flash` |
| `TECHFI_PLAYGROUND_SARVAM_V4_VOICE_ID` | `shubh_hi_customer` |
| `TECHFI_PLAYGROUND_GOOGLE_MODEL` | `chirp3-hd` |
| `TECHFI_PLAYGROUND_GOOGLE_VOICE_ID` | `hi-IN-Chirp3-HD-Achird` |
| `TECHFI_SARVAM_LLM_BASE_URL` | `https://api.sarvam.ai/v1` |
| `TECHFI_SARVAM_TTS_WEBSOCKET_URL` | `wss://api.sarvam.ai/text-to-speech/ws` |

Endpoint settings are validated to the corresponding official URL to prevent
credential redirection. Sarvam model IDs and v4 voices are validated offline.
Google is still an optional dependency (`uv sync --frozen --all-packages --extra google --group dev`).
No new dependency is required for the Sarvam experiments.

The playground snapshots its named presets independently of process-wide LLM/TTS
settings. Keep those settings valid for startup. For a headless factory caller,
Sarvam LLM requires `TECHFI_LLM_PROVIDER=sarvam` and
`TECHFI_LLM_MODEL=sarvam-105b-conversations`; v4 TTS requires
`TECHFI_TTS_PROVIDER=sarvam-v4-flash`, `TECHFI_TTS_MODEL=bulbul:v4-flash`,
`TECHFI_TTS_VOICE_ID=shubh_hi_customer`, and `TECHFI_LANGUAGE=hi-IN`.

## Manual evaluation (not executed; incurs provider charges)

The following explicitly overrides stale local model/provider settings without
changing any credentials. First run configuration-only preflight:

```sh
TECHFI_STT_PROVIDER=sarvam-realtime TECHFI_STT_MODEL=saaras:v4 \
TECHFI_LLM_PROVIDER=openai TECHFI_LLM_MODEL=gpt-4.1-mini \
TECHFI_TTS_PROVIDER=sarvam TECHFI_TTS_MODEL=bulbul:v3 TECHFI_TTS_VOICE_ID=shubh \
TECHFI_LANGUAGE=hi-IN uv run --no-sync techfi-voice --check-config
```

Preflight validates process settings only; it does not authenticate, open provider
sockets or probe experimental presets. When ready for paid manual testing, repeat
that command **without `--check-config`**, then open `http://127.0.0.1:7860`.

1. Confirm default dropdowns: GPT-4.1 Mini and Bulbul v3/Shubh. Confirm safe session
   configuration logs show `sarvam_realtime_v4`, `openai_gpt_4_1_mini`,
   `sarvam_bulbul_v3`. No key, transcript or customer data should appear.
2. Use synthetic dialogue to establish a baseline, then End. Select Sarvam 105B
   Conversations with v3 TTS; Start a new session. Check AI disclosure, identity
   remains unverified, no invented loan details, Hindi/Hinglish and respectful opt-out.
3. End, select GPT-4.1 Mini with v4 Flash; start afresh. Check voice, pronunciation
   of synthetic dates/currency, first audio and interruption both before and during
   playback. Confirm no old audio leaks into the next reply. Test Sarvam LLM + v4
   together only after these isolated comparisons. Keep both selectors locked while
   active. Test Google separately with existing ADC setup.
4. Confirm each session has its own ID/history and logs identify
   `sarvam_105b_conversations` / `sarvam_bulbul_v4_flash` when selected. Compare
   existing `llm_first_token_ms`, `tts_first_audio_ms` and
   `speech_end_to_first_audio_ms`; see [latency interpretation](latency.md).
5. End each session; verify microphone indicator/audio stop and controls unlock.
   Check disconnect/error recovery, no extra reply after opt-out, and independent
   concurrent sessions. No automatic fallback should occur.

Offline tests validate HTTP/SSE fields with an in-memory transport, socket config,
base64 PCM/context IDs, final completion, interruption reconnection, credential
routing, all six playground combinations, selector locking and cleanup. They do
not prove live provider compatibility or microphone-to-speaker behavior. No paid
requests, live sessions, commits or pushes were performed for this change.

Validation on 19 October 2026: **132 Python tests passed**, one optional local
socket integration test deselected, **8 mocked Node tests passed**. Ruff lint,
Ruff format, both JavaScript syntax checks and `git diff --check` passed.
The Python run reports 13 upstream deprecation warnings (`audioop`, Pipecat's
AudioContextTTSService declaration and Silero resource loading).
