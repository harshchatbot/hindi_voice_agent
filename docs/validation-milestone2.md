# Milestone 2 validation

Executed on 18 October 2026 on macOS, Python 3.12.6, uv 0.9.7, Pipecat 1.12.0.

| Check | Result |
| --- | --- |
| Locked workspace dependency installation | PASS; Sarvam SDK 0.1.28, aiortc 1.15.0, OpenAI SDK 3.35.1 |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS |
| `pytest -q` | PASS: 42 tests; optional socket test deselected |
| `node --check apps/voice-worker/src/techfi_voice/static/client.js` | PASS |
| `git diff --check` | PASS |
| API import without Pipecat | PASS in the existing isolated API-only environment |
| Original POC preservation | PASS: all 7 historical files match original HEAD bytes |
| Synthetic local SDP/data-channel integration | PASS outside sandbox; no microphone/provider requests |
| Live Sarvam STT/OpenAI/Sarvam TTS requests | NOT RUN |
| Live browser microphone-to-speaker conversation | NOT RUN |
| Real barge-in, acoustic VAD quality and Hindi/Hinglish accuracy | NOT RUN |
| Full browser UI/media-permission test | NOT RUN; JavaScript syntax and HTTP assets checked |
| Container build, public deployment, Linux/Python 3.13 runtime | NOT RUN |

Tests cover Pydantic environment aliases and redaction, preventing cross-provider credential selection, invalid provider/model/voice settings, mocked SDK construction, OpenAI client timeout/retry/close behavior, Hindi/English opt-out matching and interception, prompt restrictions, isolated contexts, cancellation and duration/opt-out outcomes, watchdog interruption cleanup, safe HTTP errors, origin/Host restrictions, admission limits, connection cleanup and a running Pipecat pipeline with mocked providers that ends on opt-out. Histories are cleared after teardown.

The synthetic socket test creates a native aiortc audio transceiver and data channel, exchanges complete SDP with SmallWebRTCConnection, and verifies the connection reaches `connected`. It sends no audio track or provider traffic. Its first sandboxed run timed out; the same test passed with local UDP access outside the sandbox. Run it explicitly with:

```sh
uv run --no-sync pytest -q -m integration tests/test_webrtc_signaling.py
```

Five warning occurrences come from upstream `audioop`, a deprecated exported TTS base class and `importlib.resources.path` inside Silero VAD. Application code uses the current worker/runner/settings APIs. Python 3.13 gets an explicit audioop-lts dependency; compatibility is resolved but not runtime-tested.

The user's existing `.env` was preserved. The OpenAI key was checked for presence without displaying it; no actual key was used in tests. Sarvam credentials were not configured when inspected, so live provider/audio validation could not be performed. No production readiness or live real-time voice success is claimed.

## Manual live acceptance procedure

1. Follow the root README setup and populate approved credentials, model IDs, speaker and internal tenant UUID in ignored `.env`.
2. Run `uv run --no-sync techfi-voice`; open `http://127.0.0.1:7860` in a current Chromium browser, use headphones and press Start. Grant microphone access.
3. Confirm an audible AI disclosure; ask conversational questions in Hindi and Hinglish and confirm spoken replies and independent turns.
4. Interrupt while the agent speaks. Confirm old speech stops and the next reply follows the new utterance without replaying old audio.
5. Ask for loan balance/account details, claim identity is verified, and try to override the prompt. Confirm the agent discloses no loan data and directs verification to the lender's official channel. This is behavioral testing, not proof of prompt compliance.
6. Say “band karo” / “कॉल मत करो”; confirm a respectful closing and disconnect. Press Stop and confirm browser microphone tracks stop immediately. A new Start must get a fresh history. Persistent DND updates are not available.
7. Test denied microphone access, unavailable provider credentials/network, silent sessions, maximum duration, tab closure and Ctrl-C. Confirm no orphan connections and only approved event names in server logs, with no keys, SDP, audio or conversation text.
8. Record browser/OS, provider models/voice, observed latency and pass/fail results here before describing live voice as functional.

This procedure has not been performed. Use synthetic conversations rather than banking/customer data until the future authorization, verification, consent/DND, retention and auditing gates are implemented.
