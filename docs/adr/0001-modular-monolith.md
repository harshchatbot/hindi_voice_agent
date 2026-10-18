# ADR 0001: Modular monolith and reversible migration

Status: accepted for milestone 1.

Keep one repository and one uv lock with three independently packaged Python modules. API and voice runtime are separate deployment entry points because audio lifecycle and HTTP lifecycle differ; this is not a distributed microservice architecture. No broker, persistence or service-to-service protocol is needed now. Reserve web without scaffolding Next.js.

Relocate original scripts and requirements byte-for-byte into legacy, which remains outside production imports, formatting and dependency resolution. Original relative imports work when launched from legacy. Verify preservation against Git HEAD blobs.

Use Pydantic Settings, typed application exceptions, app factories and a callable service factory for dependency injection. Pipecat FrameProcessor and transport interfaces already supply the streaming boundaries; avoid redundant STT/LLM/TTS wrapper hierarchies. Provider adapters are concrete and selected only at the integration boundary. Telephony has no implementation until required.

Consequence: local interactive voice can evolve independently from the HTTP API. The API cannot yet start a voice session. Integrating future remote transports will require authenticated policy enforcement and lifecycle coordination, not merely exposing this CLI over HTTP.
