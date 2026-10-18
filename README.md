# TechFi Labs Voice Platform

A modular monolith with an independently deployable FastAPI control plane and a Pipecat voice worker. Milestone 2 adds a minimal local browser voice client using OpenAI Whisper STT → OpenAI streamed LLM → Sarvam streaming TTS. Whisper conversations have been manually tested by the project owner; the new Sarvam Realtime path has only offline validation. Google Chirp 3 HD is an optional TTS alternative; see the [Google setup and switching guide](docs/google-tts.md). No telephony, campaigns, databases, broker or full frontend is implemented.

## Structure

```text
apps/api/             Independent FastAPI health/error handling
apps/voice-worker/    Pipecat sessions, provider integrations, local WebRTC server/client
apps/web/             Reserved for future Next.js frontend
packages/shared/      Settings, errors, opaque scopes/outcomes, safe logging
infrastructure/       API deployment recipe
docs/adr/             Architecture decisions
legacy/               Unmodified original POC scripts and historical dependencies
```

The API imports shared only; it does not depend on Pipecat. The worker owns its development signaling server rather than making the API depend on audio libraries. Production packages never import legacy. Original POC files remain byte-identical to the original Git blobs. Run historical entry points from `legacy/` using a separate environment; see [legacy notes](legacy/README.md).

## Exact startup commands

Use Python 3.12 and uv; the declared Python range remains 3.12–3.13. Only Python 3.12.6/macOS has been tested. Run everything from the repository root:

```sh
uv sync --frozen --all-packages --group dev
```

If no `.env` exists, copy `.env.example` to `.env`. **Preserve an existing `.env`** and add only the missing settings. Do not overwrite a configured key. Keep credentials local; `.env` is ignored.

Set these required variables in `.env`:

| Variable | Value to supply |
| --- | --- |
| `OPENAI_API_KEY` | Your existing OpenAI key; never sent to the browser |
| `SARVAM_API_KEY` | Sarvam key used for TTS (also used by the optional Sarvam STT adapter) |
| `TECHFI_TENANT_ID` | Internal random UUID; never a customer identifier |
| `TECHFI_STT_MODEL` | `whisper-1` for the default OpenAI STT adapter |
| `TECHFI_LLM_MODEL` | An approved streaming Chat Completions model available to your OpenAI project |
| `TECHFI_TTS_MODEL` | `bulbul:v3` for Sarvam (default); `chirp3-hd` for optional Google |
| `TECHFI_TTS_VOICE_ID` | A supported v3 speaker, for example `shubh`; Google example: `hi-IN-Chirp3-HD-Achird` |

A new internal UUID can be generated with `uv run --no-sync python -c 'from uuid import uuid4; print(uuid4())'`. Provider names default to `openai`, `openai`, `sarvam`; language defaults to `hi-IN`. Credentials use Pydantic `SecretStr`, are not included in logs, and are unwrapped only at SDK construction. Explicit `TECHFI_STT_API_KEY`, `TECHFI_LLM_API_KEY`, and `TECHFI_TTS_API_KEY` override conventional keys. A conventional key is used only for its matching provider. Google TTS uses ADC instead of a role API key; optionally set `GOOGLE_APPLICATION_CREDENTIALS` to an externally stored credential-file path. That path is loaded by Pydantic, excluded from serialization, and never logged. Other providers require their own role key. Environment variables override `.env` within Pydantic Settings source precedence.

Start the browser worker:

```sh
uv run --no-sync techfi-voice
```

Open **[http://127.0.0.1:7860](http://127.0.0.1:7860)** manually. Use a current Chromium browser, headphones and OS/browser microphone permission. In the **TechFi Labs — Voice AI Playground**, choose **Sarvam Bulbul V3 — Shubh** (default) or **Google Chirp 3 HD — Achird**, then press **Start Conversation**; the microphone is requested only then. Voice selection stays locked until the session ends. The assistant should speak its AI disclosure, followed by Hindi/Hinglish conversation. Press **End Conversation**, say **“band karo”**, or close the page to end. Audio controls let you enable playback if browser autoplay is blocked. No Node build, PortAudio, external CDN or frontend dev server is required. The browser sends only an allowlisted provider selector; model/voice presets and credentials remain on the server. See [playground setup and manual acceptance steps](docs/playground.md) for per-provider setup, local activity meters and validation limits. Server startup never prints credentials or provider payloads. Production (the default) emits only a sanitized `worker_failed` event on failure. Set `TECHFI_ENVIRONMENT=development` in your local environment or `.env` to add startup stack locations and safe validation details. Development traces omit configuration values, frame locals, source lines and arbitrary exception messages. Missing configuration is still an error; provider/model/voice and tenant settings are not silently inferred.

An offline preflight validates the same settings and constructs the application without starting a listener or creating provider clients:

```sh
uv run --no-sync techfi-voice --check-config
```

This does not validate remote credentials, model access or audio. See the [startup investigation](docs/startup-investigation.md) for the diagnosed configuration failure and fix.

To install/run the worker independently:

```sh
uv sync --frozen --package techfi-voice-worker --no-dev
uv run --no-sync techfi-voice
```

To install/run the API independently, with no provider credentials or Pipecat needed:

```sh
uv sync --frozen --package techfi-api --no-dev
uv run --no-sync uvicorn techfi_api.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --log-level critical
```

In another terminal:

```sh
curl http://127.0.0.1:8000/health/live
```

Package-specific sync changes the workspace virtual environment. Run the full workspace sync again before running tests. Both `/health/live` routes mean process liveness only; neither probes provider credentials or establishes audio readiness.

## Configuration and conversational behavior

All settings are read from `TECHFI_` environment variables / `.env` unless explicitly aliased above. Configuration validates tenant UUID, nonempty credentials, local bind address, supported language, model/voice combinations and numeric bounds before accepting browser sessions. Provider-specific model/voice validation lives at the existing integration boundary.

| Setting | Default / supported values |
| --- | --- |
| `ENVIRONMENT` | `production`; use `development` for safe local startup diagnostics |
| `TTS_PROVIDER` | `sarvam` (default); also `google`, `cartesia`. Change model and voice together when switching. |
| `LANGUAGE` | `hi-IN`; also `hi`, `en-IN`, `en`. There is no artificial `hinglish` language code. |
| `STT_STREAM_TYPE` | `balanced`; also `fast`, for `sarvam-realtime` only |
| `STT_KEEPALIVE_SECONDS` | 10, bounded to 1–60; realtime WebSocket keepalive |
| `STT_MODE` | `transcribe`; also `codemix`, `verbatim`, `translit` |
| `HOST`, `PORT` | `127.0.0.1`, `7860`; only `localhost` or `127.0.0.1` binding is accepted |
| `MAX_SESSIONS` | 4, bounded to 1–16 |
| `MAX_SESSION_SECONDS` | 600 |
| `CONNECTION_TIMEOUT_SECONDS` | 20 for SDP exchange and Pipecat setup/start |
| `PROVIDER_TIMEOUT_SECONDS` | 20 for OpenAI HTTP reads and STT/LLM/TTS progress watchdogs |
| `CLEANUP_TIMEOUT_SECONDS` | 10 per teardown phase |
| `IDLE_TIMEOUT_SECONDS` | 90 |
| `OPT_OUT_GRACE_SECONDS` | 8 maximum to speak the closing response |
| `VAD_CONFIDENCE` | 0.7 |
| `VAD_START_SECONDS`, `VAD_STOP_SECONDS` | 0.2, 0.4 |
| `TURN_SILENCE_SECONDS` | 0.6; also waits for STT finalization/latency metadata |
| `SYSTEM_PROMPT` | Polite loan-payment reminder style in Hindi/Hinglish |
| `GREETING`, `OPT_OUT_REPLY` | AI disclosure / respectful closing, configurable as approved text |
| `OPT_OUT_PHRASES` | JSON array; defaults include English, romanized Hindi and Devanagari requests |

Use the full names, e.g. `TECHFI_PROVIDER_TIMEOUT_SECONDS`, in `.env`. Hinglish recognition quality and voice quality depend on the chosen providers/models and still need a live evaluation. The local Silero VAD and Pipecat speech-timeout turn strategies drive endpointing. Whisper `whisper-1` uses Pipecat's supported OpenAISTTService to transcribe VAD-delimited utterances over HTTP; it is not a streaming STT WebSocket. The browser audio transport remains WebRTC. For Saaras V4 realtime streaming, select `sarvam-realtime`; see the [realtime STT setup and manual switching guide](docs/sarvam-realtime-stt.md). The default remains Whisper. When selecting the older `sarvam` streaming STT adapter, set its model to `saaras:v3` or `saaras:v4`; Sarvam server VAD is disabled to avoid competing turn detection. Pipecat broadcasts interruptions when local speech starts; TTS/output queues and LLM generation receive interruption frames. The monitor clears stale watchdogs on interruption. OpenAI LLM retries are disabled, HTTP reads are bounded, and both OpenAI clients close during cleanup.

The default prompt is in `techfi_voice/prompt.py`. It is supplied through the LLM service’s supported `system_instruction` setting; session history starts empty. `TECHFI_SYSTEM_PROMPT` controls conversational style; mandatory disclosure, identity, privacy and opt-out instructions are appended. The greeting discloses AI status before any loan discussion. **No identity verifier or customer/loan data source exists:** identity stays unverified, and the assistant must direct requests for details to the lender's official verification channel. It must not infer or invent balances, due dates or even the existence of a loan. A spoken name or “yes” does not grant access. This is a reminder conversation demo, not a verified account service or payment collector.

Explicit matching opt-out transcripts are intercepted before the LLM, interrupt current output, speak a fixed respectful acknowledgment and end the session. The spoken closing has a bounded grace period. The Stop button stops microphone tracks immediately. Phrase matching is conservative and is not a complete multilingual intent classifier; the prompt also directs the LLM to respect other refusal/wrong-number requests. Do not infer persistent DND registration: no opt-out database or future-call scheduler exists.

## Sessions, transport and cleanup

The existing callable service factory supplies Pipecat processors; `build_session` receives the factory and transport. Each connection owns separate STT/LLM/TTS instances, context, policy processor, VAD, turn strategies, monitor and worker. No conversation history or customer-specific state is global. Histories are memory-only and cleared at teardown.

The browser uses native WebRTC against Pipecat `SmallWebRTCConnection` / `SmallWebRTCTransport`. Audio uses 16 kHz input and 24 kHz output internally. SDP is exchanged once, after ICE gathering; a data channel sends keepalive pings. No STUN/TURN servers are configured: this is local-machine testing. A failed/ended session requires a new Start; ICE restart and remote deployment are deliberately outside this client.

The runtime supervisor owns duration, opt-out grace, cancellation and cleanup tasks. Provider error frames or progress deadlines fail the session without serializing payloads. Browser disconnect, idle timeout, explicit Stop and server shutdown end sessions. Connection teardown and history clearing run on success and failure. Outcomes use the shared typed contract (`completed`, `cancelled`, `failed`, `opted_out`, `timed_out`) and are not persisted. Local signaling routes enforce loopback hosts, same-origin requests and a custom header, with no cross-origin access. These developer controls are not production tenant authentication: do not expose this server through a public tunnel or reverse proxy.

## Extending providers

Keep SDK imports/provider names inside `integrations/` and configuration validation. Add the concrete Pipecat processor and select it in the existing factory; no duplicate STT/LLM/TTS protocol hierarchy is needed. The `sarvam` STT and earlier `deepgram`, `groq`, `cartesia` adapters remain selectable, including mixed combinations; supply their own role keys and supported model/voice IDs. Their remote functionality remains untested in this milestone. Google TTS is installed with `uv sync --frozen --all-packages --extra google --group dev` and imported only when selected. Its adapter retains Pipecat streaming and closes the session-owned gRPC channel during cleanup. See [authentication, setup and manual switching commands](docs/google-tts.md). Shared has no audio/provider dependencies.

Before any external calling entry point, implement server-authenticated tenant scope, the existing `CallAuthorization` boundary (consent, DND, permitted hours), a reviewed identity-verification mechanism and durable outcome auditing. No allow-all calling policy or dialer is supplied.

## Versions and source references

The single `uv.lock` pins compatible transitive versions. This milestone retains **Pipecat 1.12.0**, with its `sarvam` and `webrtc` extras: Sarvam SDK **0.1.28**, aiortc **1.15.0**, OpenAI SDK **3.35.1**. The worker adds the `audioop-lts` compatibility dependency for Python 3.13 because this Pipecat release imports `audioop`; Python 3.13 has not been runtime-tested. Use current `Settings`, `PipelineWorker`, `WorkerRunner.add_workers/run`, and universal context aggregators. No deprecated `InputParams`, `PipelineTask` or `PipelineRunner` API is used by application code.

Integration choices were checked against the installed pinned source and official [Sarvam STT integration](https://docs.pipecat.ai/api-reference/server/services/stt/sarvam), [Pipecat source](https://github.com/pipecat-ai/pipecat/tree/v1.12.0/src/pipecat), and [OpenAI streaming Chat Completions contract](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create). Recheck models and compatible SDK pins before upgrades.

## Validation and data handling

```sh
uv sync --frozen --all-packages --group dev
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -q
node --check apps/voice-worker/src/techfi_voice/static/client.js
node --check tests/test_playground_client.cjs
node --test tests/test_playground_client.cjs
```

Optional local SDP/data-channel check (synthetic transceiver, no microphone or provider requests):

```sh
uv run --no-sync pytest -q -m integration tests/test_webrtc_signaling.py
```

See [milestone 2 validation](docs/validation-milestone2.md), [startup fix validation](docs/startup-investigation.md), and [architecture decisions](docs/adr). Synthetic tests do not establish that credentials, streaming recognition, spoken output, barge-in or Hindi/Hinglish quality work live.

Logs contain approved event names only; unknown messages, exception text and extras are suppressed. Default Pipecat/SDK logging sinks and HTTP access logs are disabled. No raw error payloads or transcripts are returned to the browser. Audio is not recorded; browser/client code logs no SDP or conversations. Native provider/usage metrics, tracing and RTVI transcript forwarding remain disabled. Content-free monotonic latency events are enabled; see [latency definitions and bottleneck guide](docs/latency.md). Provider processing still sends audio/text off-machine: review retention/residency contracts and secret management before using any customer data. These controls do not constitute banking or loan-recovery compliance.
