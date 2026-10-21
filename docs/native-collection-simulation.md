# Native Soft Collection simulation (macOS development only)

This separate executable uses Pipecat 1.12.0 `LocalAudioTransport` (optional
`local` extra, PyAudio 0.2.14 and PortAudio). It opens the default native microphone
and speaker. It starts **no HTTP server, browser, WebRTC, ICE, telephony or inbound
network listener**. There is no production admission-policy bypass.

The production `techfi-voice` entry point and its collection admission/window
checks are unchanged. Native startup uses a separate configuration and calls the
shared pipeline assembly directly with its own fresh `CollectionWorkflow`.
It never constructs production admission, retry or suppression stores. Each
process supports one conversation; closing/opt-out ends it. Results live only in
memory and a local structured result printed at exit. No results are written to
production storage. Restarting explicitly starts a new fictional test.

## Setup and startup

From the repository root, in an interactive local macOS terminal:

```sh
brew install portaudio
uv sync --frozen --package techfi-voice-worker --extra native
```

Keep the existing `SARVAM_API_KEY` in the local ignored `.env`, or supply it through
your normal secure environment mechanism. Do not paste the key into commands or
source. This runner reads only that credential and `TECHFI_NATIVE_ENVIRONMENT`
from `.env`/environment. It ignores production `TECHFI_*` provider, record, prompt,
endpoint, tenant and policy overrides. No real customer-data input is supported.
Do not use `--all-extras`; native audio does not require Google dependencies.

Validate configuration without constructing audio devices or provider clients:

```sh
TECHFI_NATIVE_ENVIRONMENT=development uv run --no-sync --package techfi-voice-worker techfi-collection-sim --development --fictional-data-only --check-config
```

The following commands **start cloud processing and may incur provider charges**.
Run them only when ready for explicitly initiated manual fictional testing:

```sh
TECHFI_NATIVE_ENVIRONMENT=development uv run --no-sync --package techfi-voice-worker techfi-collection-sim --development --fictional-data-only --persona male
```

For the female persona, replace `--persona male` with `--persona female`.
The fixed provider choices are Sarvam Realtime Saaras v4 (16 kHz PCM,
manual endpointing, balanced), Sarvam `sarvam-105b-conversations` and Bulbul v3
(Shubh male / Ritu female). This reuses the existing adapters, including the
[Sarvam LLM limitations](sarvam-experimental.md). Other playground options remain
unchanged; native configuration deliberately does not accept arbitrary model,
voice, endpoint, tool or integration settings.

The explicit flags and `TECHFI_NATIVE_ENVIRONMENT=development` are all required.
Default environment is production, which refuses native startup. The native runner
rejects non-macOS, noninteractive stdin/stdout and detected SSH sessions. The
production executable does not accept these flags. Missing PyAudio/PortAudio or
native audio startup failures end the run; there is no browser/network fallback.
`--check-config` checks settings only, not audio dependency availability, device
access, provider credentials' validity or microphone permission.

## macOS microphone and audio

Choose the intended input/output in **System Settings → Sound** before starting.
Use headphones: this transport has no browser acoustic echo cancellation, and
speaker feedback can cause false interruptions. Hardware must support mono PCM
capture at 16 kHz and playback at 24 kHz through PortAudio.

On first microphone access, macOS may request permission for Terminal, iTerm or the
hosting application. Allow it only for the application you intentionally use.
If denied, enable that application under **System Settings → Privacy & Security →
Microphone**, then restart the terminal/runner if necessary. Managed-device policy
may require administrator help. This implementation does not change permissions
or access a device during configuration checks. Unavailable/denied devices fail
closed without starting an alternative transport.

## Fictional data and conversation boundary

There is one immutable, server-owned Python fixture, `fictional-emi-001`:
Demo Prerna Bank, Demo Asha Verma, INR 4500.00, due 2026-10-25, personal loan,
status due, approved methods official app and bank branch. The scenario calendar
is fixed to 2026-10-19 for reproducible promise-date tests. No file, URL, customer
record, fixture override, account identifier or payment link can be supplied by
CLI or environment. The fixture is never sent to the LLM as customer context.

The greeting identifies the configured persona, fictional organization and
AI assistant. The existing exact finalized demo phrase **neela kamal / नीला कमल**
is required before disclosure; it is public fictional verification, not real
identity proof. Existing application-owned response templates, outcome confirmation,
wrong-recipient handling, opt-outs, interruption fences and privacy rules remain.
Untrusted spoken input cannot authorize integrations, record updates or invented
financial terms. The LLM has no tools and cannot perform transfers or payments.

This is application capability separation, **not an OS sandbox**. The runner
has no CRM, customer-record, payment or dialing action path, nor configuration to
load one. It still has ordinary Python process permissions and provider network
access. A person modifying code or injecting Python modules can change that;
this executable is not a security boundary against a hostile local operator.
It does not attest physical device provenance, detect every virtual-audio driver,
or prevent remote-desktop audio routing. Use a trusted Mac, local terminal and
physical devices; do not route real calls/customer audio into the microphone.
Unlike browser simulation, it does not claim to prove browser/proxy provenance:
there is no browser or inbound signaling/media surface to protect.

All microphone audio and recognized speech are potentially sensitive even with
fictional fixtures: use fictional utterances only. Existing structured logs omit
audio, transcripts, credentials and customer facts. The terminal exit report is
explicit fictional structured outcome data, including a reported promise date;
it is not a payment confirmation and does not modify a financial record. Ctrl-C
cancels pending proposals, cleans context/audio and reports the cancellation result
when a session has been created. Initialization failures use a generic safe error.

The worker retains its existing monotonic per-session/per-turn latency observer.
First server audio/transport handoff measures when PCM reaches the native output
processor, not when a physical speaker becomes audible. Greeting audio has no user
turn to attribute. See [latency documentation](latency.md).

## Offline validation and manual plan

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
node --check apps/voice-worker/src/techfi_voice/static/client.js
node --test tests/test_playground_client.cjs
```

Native tests run the real shared Pipecat pipeline and runner with mocked
STT/LLM/TTS, synthetic PCM and fake native devices. They cover both personas from
greeting to confirmed promise outcome, fixture immutability, ignored production
environment data, independent workflows, unauthorized startup, initialization
cleanup and production entry-point rejection. Existing collection tests cover
interrupted/stale replies, disclosure gates and production calling-hour enforcement.
No microphone, audio playback or paid endpoint is exercised by these tests.

After offline checks, manually:

1. Run config validation. Missing opt-in, production environment and SSH/redirected
   execution must refuse startup before providers/devices are created.
2. Select headphones and grant microphone permission, then explicitly start a run
   outside production calling hours. Observe the fictional simulation notice.
3. Before verification, request amount/customer details and interrupt the greeting:
   no loan facts should be revealed. Restart for a separate wrong-recipient test.
4. In a fresh run, say the demo phrase, hear the exact fixture details, request a
   promise for 26 October 2026, then confirm. Inspect the fictional outcome report.
5. Repeat female persona, reject a proposed outcome, test an interruption during a
   proposal, then request “do not call”. No interruption should confirm a proposal;
   opt-out must close without further payment demands.
6. Test Ctrl-C, permission denial and unavailable devices; verify release of the
   microphone and safe failure. Compare native latency logs with prior browser
   logs while remembering the different output/device buffering boundaries.
7. Separately confirm normal collection admission still rejects outside its window;
   do not change its enablement, hours, counters or system clock for this test.

Live audio, device permission behavior, acoustic interruptions, provider recognition
and synthesis quality remain **untested** until these manual steps succeed.
