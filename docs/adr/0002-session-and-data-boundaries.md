# ADR 0002: Per-session state and fail-closed data handling

Status: accepted for milestone 1.

Create a fresh context, processor graph, provider service set and opaque scope for each session. No process-global conversation memory, mutable tenant state or common audio filenames. The local microphone is a single-user transport; concurrent calling is not implemented.

Use an immutable tenant/session scope and typed call outcome contract. Keep a minimal authorization protocol for future consent/DND policy. No allow-all implementation and no unauthenticated call-start endpoint are supplied. Local tenant configuration represents trusted developer input only. Authentication-derived scope, enforced consent/DND/allowed-hours decisions and durable outcome audit are release gates for outbound calls.

Application logging serializes a fixed event enumeration only. Third-party logs are suppressed because even error messages can contain credentials or transcripts. No recording or durable conversation retention. This sacrifices provider debugging detail to protect data; safe diagnostic codes can be added deliberately later. Provider processing/retention remains a separate contractual control.
