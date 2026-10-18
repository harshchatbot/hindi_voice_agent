# Milestone 1 validation

Executed on 18 October 2026 on macOS with Python 3.12.6 and uv 0.9.7.

| Check | Result |
| --- | --- |
| Workspace dependency resolution | PASS: uv.lock resolves 76 packages |
| Full workspace install | PASS: API, shared, Pipecat and local/provider extras installed |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS: workspace files; legacy excluded |
| `pytest -q` | PASS: 10 tests, 6 upstream deprecation warnings |
| Legacy preservation | PASS: all six scripts and requirements match HEAD bytes |
| Independent backend installation | PASS: separate temporary environment with 18 API/shared dependencies, no Pipecat |
| Independent backend app factory import | PASS |
| API health and error responses | PASS through in-process FastAPI TestClient |
| Pipecat real provider constructors | PASS with synthetic credentials; no provider requests |
| Session isolation | PASS: separate contexts, processors and session identifiers |
| Pipecat runner lifecycle | PASS: synthetic forwarding processor, Start/End lifecycle, no audio/network |
| Live Hindi/Hinglish audio conversation | NOT RUN: no credentials/audio authorization supplied |
| Container build / deployment | NOT RUN |
| Python 3.13 / Linux runtime | NOT RUN |
| Legacy runtime/audio execution | NOT RUN |

The six warnings originate inside Pipecat/dependencies: Python audioop deprecation, a deprecated base TTS class exported upstream, and importlib.resources.path usage in VAD/turn model loading. The new application uses current worker/runner/settings interfaces. Warnings are not suppressed by the test suite.

The initial synthetic lifecycle test did not forward frames and stalled; it was corrected to use a forwarding processor and explicitly assert the pipeline-finished event. The final 10-test run completed in 1.31 seconds.

No end-to-end voice functionality or production readiness is claimed. Constructor and synthetic lifecycle tests establish compatibility of imported interfaces only. Before claiming live voice works, configure approved credentials/models/voice, run `techfi-voice` with an authorized microphone and headphones, and verify Hindi plus Hinglish recognition, spoken replies, interruption, timeout, Ctrl-C cleanup, provider outage handling and absence of sensitive logs. Recheck privacy contracts before using customer data.
