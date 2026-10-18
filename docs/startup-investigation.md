# Startup failure investigation and fix

Inspected on 18 October 2026. All verification was offline; no API requests, listening server, microphone or socket-level integration test was started.

## Exact cause

The installed `techfi-voice` console script imports `techfi_voice.main:main`. `main()` calls `VoiceSettings()` before `create_app()` or Uvicorn. Pydantic raised `ValidationError` for five required settings missing from the local environment / `.env`:

- `TECHFI_TENANT_ID`
- `TECHFI_STT_MODEL`
- `TECHFI_LLM_MODEL`
- `TECHFI_TTS_MODEL`
- `TECHFI_TTS_VOICE_ID`

The top-level broad exception handler emitted `Event.WORKER_FAILED` and exited with code 1. SafeFormatter intentionally serialized only its event name and level, hiding the missing-settings diagnosis. This was not a package-installation failure or a credential rejection.

The `.env` file was found from the repository root and both conventional provider key fields loaded successfully. No credential was printed. A second issue was the provider mismatch: the default STT was Sarvam, and the factory did not support OpenAI Whisper.

## Minimal correction

Append only missing non-secret configuration to the existing ignored `.env`. Select `openai` STT / `openai` LLM / `sarvam` TTS, use `whisper-1`, `gpt-4.1-mini`, `bulbul:v3`, speaker `shubh`, and a newly generated internal UUID for local development. Enable development diagnostics in that local file. All original file bytes remain intact as a prefix, and all original parsed values—including credential values—were asserted unchanged. Tests and preflight made no further changes to `.env`.

The model/voice selections are local configuration, not a claim that the remote accounts allow those models. Real tenant identity must be configured for a real deployment; the local UUID has no external authorization meaning.

Add OpenAISTTService at the existing integration boundary, with Whisper-specific model validation and routing of OPENAI_API_KEY to STT and LLM only. Retain interchangeable Sarvam/Deepgram STT and existing LLM/TTS adapters. Configure the supported Whisper HTTP-client injection with the provider timeout and close its SDK client during cleanup. Whisper performs utterance-based transcription at VAD boundaries, not a streaming transcription socket.

## Development and production errors

`StartupSettings` independently loads `TECHFI_ENVIRONMENT` so development diagnostics work even when the full voice configuration fails. Production is the default and still logs only the approved structured event. Development adds exception type, stack file/line/function locations and safe validation field/error codes. Configuration values, arbitrary exception messages, source lines, frame locals and validation context are omitted in both modes. No third-party debug sinks or access logs are enabled.

`--check-config` validates settings and application construction, then exits without starting Uvicorn or creating provider clients. It does not check remote credentials, microphone access or conversation quality.

## Changed files

- `apps/voice-worker/src/techfi_voice/main.py`: bootstrap diagnostics and offline preflight.
- `apps/voice-worker/src/techfi_voice/config.py`: production/development selection, OpenAI STT default and secure key routing.
- `apps/voice-worker/src/techfi_voice/diagnostics.py`: safe development startup trace formatter.
- `apps/voice-worker/src/techfi_voice/integrations/providers.py`: Whisper validation and construction.
- `apps/voice-worker/src/techfi_voice/integrations/openai.py`: supported Whisper HTTP timeout and client cleanup.
- `tests/test_voice_startup.py`: seven offline regression cases, including both log modes.
- `.env`: missing non-secret settings appended; credentials untouched.
- `.env.example`, `README.md`: intended stack, diagnostics and preflight instructions.
- `docs/startup-investigation.md`: this record. Historical milestone validation records remain historical.

## Results

- Offline pytest suite: **49 passed**, 1 socket integration test deselected, 5 upstream deprecation warnings.
- Ruff lint: **passed**.
- Ruff format check: **passed**.
- Actual local `.env` Pydantic load and key routing assertions: **passed**, with values withheld.
- Actual local startup preflight: **passed**, with Uvicorn and provider-client construction patched to fail if invoked.
- No dependency change or installation was needed.
- No paid API call, WebRTC socket integration, listening server or live voice session was run.

After reviewing these results, the offline next command from the repository root is:

```sh
uv run --no-sync techfi-voice --check-config
```

When you choose to perform live testing later, `uv run --no-sync techfi-voice` starts the local server. Live audio and remote model/credential access remain unverified.
