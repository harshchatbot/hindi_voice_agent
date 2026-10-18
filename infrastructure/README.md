# Deployment

Build the backend from the repository root with `docker build -f infrastructure/Dockerfile.api -t techfi-api .`. The image installs only API/shared dependencies and runs under an unprivileged UID. It does not install Pipecat or voice SDKs. The container recipe has not been build-tested in these milestones.

The worker is separately installable: `uv sync --frozen --package techfi-voice-worker --no-dev`, then `uv run --no-sync techfi-voice` from the repository root after configuring `.env`. It serves a minimal local browser WebRTC client on loopback; the default address is `http://127.0.0.1:7860`. There is no PortAudio or Node build requirement. This is a development transport: do not publish it via a tunnel or proxy. See the root README for configuration and exact validation commands.

No telephony, public worker deployment, Kubernetes, database, Redis or Celery is included. Production remote transport/authentication, tenant authorization, consent/DND, data-retention decisions and durable audit outcomes remain future release gates.
