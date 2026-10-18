# Google Chirp 3 HD streaming TTS

`TECHFI_TTS_PROVIDER=google` selects Pipecat's `GoogleTTSService` streaming
implementation, with a small subclass for ADC file loading and gRPC cleanup.
Sarvam Bulbul V3 remains the default. The STT/LLM factories, system prompt,
conversation history, frontend and telephony boundaries are unchanged.

The example voice is `hi-IN-Chirp3-HD-Achird`. `TECHFI_TTS_VOICE_ID` accepts
other Chirp 3 HD voice names matching the configured language. Validation checks
the voice family and language prefix offline; it does not query Google's voice
catalogue or guarantee project/voice availability. `TECHFI_TTS_MODEL=chirp3-hd`
is the adapter's validated family label; Google's synthesis API selects the
model through the voice name, so no separate model parameter is sent.

## Install the optional dependencies

From the repository root, using Python 3.12 and uv:

```sh
uv sync --frozen --all-packages --extra google --group dev
```

For an independent worker installation without development dependencies:

```sh
uv sync --frozen --package techfi-voice-worker --extra google --no-dev
```

The API does not acquire Google dependencies. A normal sync without `--extra
google` keeps Google optional and may remove these packages; include the extra
again whenever syncing an environment that should support Google TTS.
Pipecat remains pinned to **1.12.0**. The lockfile adds Google Cloud
Text-to-Speech **2.38.0**, Google Auth **2.62.0**, Google GenAI **2.31.0** and
gRPC **1.84.0** through Pipecat's supported `google` extra (which also includes
Google Speech dependencies). Its compatible WebSocket pin is **16.1.1**.

## Google Cloud setup: local ADC

These are manual setup instructions; no cloud resources or credentials were
created during implementation, and no synthesis requests were made.

1. Create or select a Google Cloud project in the Console and enable billing.
   Install the [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install).
   Use an account authorized to enable APIs on that project.
2. Replace `YOUR_PROJECT_ID` below, initialize the CLI and enable Cloud TTS:

   ```sh
   export TECHFI_GOOGLE_PROJECT_ID=YOUR_PROJECT_ID
   gcloud init
   gcloud config set project "$TECHFI_GOOGLE_PROJECT_ID"
   gcloud services enable texttospeech.googleapis.com --project="$TECHFI_GOOGLE_PROJECT_ID"
   ```

3. Create local Application Default Credentials and set the quota project:

   ```sh
   gcloud auth application-default login
   gcloud auth application-default set-quota-project "$TECHFI_GOOGLE_PROJECT_ID"
   ```

   CLI login alone does not configure ADC. The authenticated identity needs
   `serviceusage.services.use` on the quota project. If necessary, a project
   administrator can grant the Service Usage Consumer role:

   ```sh
   gcloud projects add-iam-policy-binding "$TECHFI_GOOGLE_PROJECT_ID" \
     --member="user:YOUR_GOOGLE_ACCOUNT_EMAIL" \
     --role="roles/serviceusage.serviceUsageConsumer"
   ```

4. Leave `GOOGLE_APPLICATION_CREDENTIALS` absent from both your shell and `.env`
   when using this local ADC file. A configured credentials path takes precedence.
   No Google API key or `TECHFI_TTS_API_KEY` is required. Preserve existing
   OpenAI/Sarvam keys for the selected STT/LLM and for switching back to Sarvam.

See Google's [Cloud TTS authentication guide](https://docs.cloud.google.com/text-to-speech/docs/authentication)
and [ADC quota-project troubleshooting](https://docs.cloud.google.com/docs/authentication/troubleshoot-adc#user-creds-client-based).

## Service accounts and deployment credentials

For a Google Cloud workload, prefer ADC from its attached service account.
For local service-account testing, use impersonation rather than downloading
a private key. An administrator creates the account and authorizes the caller:

```sh
gcloud iam service-accounts create techfi-voice-tts --project="$TECHFI_GOOGLE_PROJECT_ID"
export TECHFI_GOOGLE_TTS_ACCOUNT="techfi-voice-tts@$TECHFI_GOOGLE_PROJECT_ID.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$TECHFI_GOOGLE_PROJECT_ID" \
  --member="serviceAccount:$TECHFI_GOOGLE_TTS_ACCOUNT" \
  --role="roles/serviceusage.serviceUsageConsumer"
gcloud iam service-accounts add-iam-policy-binding "$TECHFI_GOOGLE_TTS_ACCOUNT" \
  --project="$TECHFI_GOOGLE_PROJECT_ID" \
  --member="user:YOUR_GOOGLE_ACCOUNT_EMAIL" \
  --role="roles/iam.serviceAccountTokenCreator"
gcloud auth application-default login --impersonate-service-account="$TECHFI_GOOGLE_TTS_ACCOUNT"
gcloud auth application-default set-quota-project "$TECHFI_GOOGLE_PROJECT_ID"
```

Use the organization's approved workload identity configuration for deployments
outside Google Cloud. If using an approved service-account/ADC JSON file, keep
it outside this repository and mount it securely. Set only its absolute path:

```sh
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/secure/path/google-credentials.json
chmod 600 "$GOOGLE_APPLICATION_CREDENTIALS"
```

The path may instead be supplied through ignored `.env` as
`GOOGLE_APPLICATION_CREDENTIALS`; Pydantic loads it without modifying the process
environment. The adapter uses Google's credential-file loader, including ADC
file types, and never logs the path or credential contents. With no path, native
Google ADC discovery is used. Do not commit JSON, paste it into `.env`, embed it
in an image, or use `TECHFI_TTS_API_KEY` for Google authentication. Local
`credentials/`, `secrets/` and common credential filenames are excluded from
Git/build contexts; external storage remains the intended setup.

## Switch manually between Google and Sarvam

The browser playground now has its own allowlisted voice selector, defaulting
to Sarvam. Use that dropdown for browser sessions; its server-owned preset
settings and credential routing are described in the [playground guide](playground.md).
The process-wide settings below remain required for startup and direct factory
use; they do not override the browser selection.

Stop the worker and finish active sessions before switching. Keep the current
STT and LLM settings as they are. The following examples assume the existing
language is `hi-IN` (or `hi`, which is normalized to `hi-IN` for Google TTS).

Validate Google configuration without provider clients or requests:

```sh
TECHFI_TTS_PROVIDER=google TECHFI_TTS_MODEL=chirp3-hd TECHFI_TTS_VOICE_ID=hi-IN-Chirp3-HD-Achird uv run --no-sync techfi-voice --check-config
```

When ready for a separately authorized manual conversation:

```sh
TECHFI_TTS_PROVIDER=google TECHFI_TTS_MODEL=chirp3-hd TECHFI_TTS_VOICE_ID=hi-IN-Chirp3-HD-Achird uv run --no-sync techfi-voice
```

Open the existing local browser client at `http://127.0.0.1:7860`. Starting a
conversation can incur charges for the selected providers; the preceding
offline check does not validate ADC, IAM, voice access, quota or audio.

To return to the existing Sarvam example, stop the worker and validate:

```sh
TECHFI_TTS_PROVIDER=sarvam TECHFI_TTS_MODEL=bulbul:v3 TECHFI_TTS_VOICE_ID=shubh uv run --no-sync techfi-voice --check-config
```

Then start with:

```sh
TECHFI_TTS_PROVIDER=sarvam TECHFI_TTS_MODEL=bulbul:v3 TECHFI_TTS_VOICE_ID=shubh uv run --no-sync techfi-voice
```

Substitute your existing approved Bulbul V3 voice for `shubh` if different.
Sarvam requires `SARVAM_API_KEY` or its explicit role-key override. To persist
a selection, set the same three `TECHFI_TTS_*` values in local `.env`; shell
variables override `.env`. Switching only the provider while leaving another
provider's model/voice configured is rejected. No active session switches or
automatic fallback are implemented.

## Behavior, metrics and validation limits

Google's bidirectional streaming synthesis uses gRPC and emits mono 24 kHz PCM,
matching the existing output transport. Each session owns a new service/client;
cleanup closes its gRPC transport. Existing VAD interruption handling and bounded
provider/session/cleanup watchdogs apply. Errors use the existing typed/sanitized
boundary, without forwarding raw Google error payloads to logs or the browser.
There is no new synthesis request for diagnostics.

The same supported TTS request event and context-correlated audio frames feed
[latency instrumentation](latency.md), including `tts_first_audio_ms` and
`speech_end_to_first_audio_ms`. This measures Pipecat's first emitted audio chunk,
which includes buffering, rather than raw gRPC first-byte latency or browser
audibility. The adapter doesn't enable SSML, voice cloning or prompt changes.

Run offline validation with the optional dependencies installed:

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
```

Google-specific SDK tests skip when the extra is absent; configuration tests
still run. Tests mock ADC, credential loading and the streaming client and make
no paid requests. Live Hindi/Hinglish quality, regional availability, permissions,
quota, interruption behavior against Google and microphone-to-speaker latency
remain untested.

Official integration references: [Pipecat Google TTS](https://docs.pipecat.ai/api-reference/server/services/tts/google)
and [Google Chirp 3 HD voices and streaming](https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd).
