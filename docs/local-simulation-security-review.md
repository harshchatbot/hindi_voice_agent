# Local simulation security review — fail-closed decision

> Browser/WebRTC review remains applicable. A separate native microphone/speaker
> runner is now available; see [native simulation](native-collection-simulation.md).
> It has no incoming signaling/media listener and does not implement the browser
> mode proposed below or change production admission.

Reviewed 19 October 2026 against the installed worker, Pipecat 1.12.0 and its
locked Uvicorn/aiortc/aioice dependencies. **Browser/WebRTC local simulation is not implemented.**
The requested reliable local-only execution and browser/media boundary cannot be
established by the current application interfaces. No calling-hours/retry bypass,
simulation authorization flag, misleading playground option or weakened production
policy was added. Existing uncommitted work is preserved.

## Blocking evidence

1. `apps/voice-worker/src/techfi_voice/server.py` uses TrustedHostMiddleware and
   checks Host/Origin agreement plus `X-TechFi-Client: local-browser`. These are
   useful browser request checks, not proof of client identity or local execution.
   A reverse proxy can generate the same headers.
2. Even an unmodified socket peer address of `127.0.0.1` cannot distinguish a
   direct local browser from a reverse proxy or tunnel terminating on that machine.
   The ASGI HTTP request does not carry an authenticated browser process identity
   or deployment attestation. Environment=`development`, a CLI flag, platform name
   or container-environment heuristics cannot provide that missing proof.
3. `apps/voice-worker/src/techfi_voice/main.py` uses `uvicorn.run` without explicitly
   disabling proxy-header handling. The installed Uvicorn defaults enable it and
   trust loopback proxy peers. Its ProxyHeadersMiddleware can rewrite ASGI client
   metadata from forwarded headers. Disabling that behavior would remove this
   particular ambiguity, but would still not distinguish a local browser from a
   same-machine reverse proxy. It would not satisfy the requested isolation alone.
4. Pipecat SmallWebRTCConnection accepts ICE servers and a connection timeout; its
   constructor does not expose a loopback-only media interface restriction. It
   constructs aiortc's RTCPeerConnection using RTCConfiguration(iceServers=...).
   Empty ICE servers disable configured STUN/TURN servers, not host candidates.
   The installed aioice `get_host_addresses` explicitly excludes `127.0.0.1` and
   `::1`, retains other eligible interface addresses, and creates UDP sockets on
   those addresses during candidate gathering. HTTP loopback binding therefore
   does not provide loopback-only WebRTC media containment.

These findings come from local installed source, not provider API requests. An
in-memory check, without sockets, verified both the forwarded-client metadata
behavior and the ICE address enumeration using synthetic adapter addresses.

## Why an application-only bypass was rejected

Combining a development label, explicit flag, Host/Origin checks, a loopback peer
check and container detection would still allow an ambiguous local proxy/tunnel
path. A browser token alone authenticates possession; it does not establish the
required deployment or network provenance. No token, cookie or spoofable header is
being substituted for the missing local-only execution boundary.

The existing `DemoCase.fictional=true` assertion and editable `Demo ...` names are
also not a server-owned fixture catalog. A real simulation implementation would
need a closed server fixture-ID allowlist, rejection of browser-supplied case data,
and its own retry/suppression/outcome stores. Those changes are feasible, but do
not resolve the HTTP/WebRTC isolation blockers and were not shipped as an
apparently functional mode.

The existing workflow has no bank/CRM/payment/telephony action implementation.
That fact is preserved. A future simulation runner must enforce an explicit
capability boundary rather than assuming future integration additions stay absent.
Only the selected STT/LLM/TTS services should be available for voice processing.

## Boundary needed before enabling simulation

A future implementation needs an OS-enforced, independently verifiable local
execution boundary covering **both browser signaling and WebRTC media**. For
example, a narrowly scoped desktop runner with authenticated local IPC and enforced
network isolation could supply that boundary. It must prove that public container
port publication, same-host reverse proxies and network tunnels cannot reach the
simulation path. An ordinary loopback HTTP listener or self-reported environment
variable cannot attest this. The media restriction also needs a supported transport
extension or a separately reviewed runtime containment mechanism; changing or
monkeypatching ICE internals is not a safe minimal substitute.

Once that boundary exists, the simulation mode can safely add:

- explicit startup opt-in plus `TECHFI_COLLECTION_SIMULATION_ENABLED=false` default;
- fixed server-approved fictional fixtures selected by IDs only;
- separate in-memory simulation admission/suppression/outcome state;
- bypass of only calling hours, daily attempts and retry gaps;
- unchanged disclosure, verification, suppression, transcript, interruption and
  provider controls;
- a distinct playground option and persistent SIMULATION — FICTIONAL DATA banner;
- negative tests against remote/proxied/container access and media exposure.

This is an additional security/runtime scope, not an implemented capability.
No privileged networking changes, native launcher installation, live server,
provider calls or live voice testing were performed for this review.

## Startup and manual testing status

There is **no approved any-hour browser simulation startup command** in the current code.
The separate native runner linked above has its own documented startup commands.
Do not use a development label, fake forwarded headers, changed system clock,
container/host override or broadened collection hours to simulate authorization.
`--local-simulation` and a simulation Agent Type are not implemented, and no
`TECHFI_COLLECTION_SIMULATION_ENABLED` setting has been registered. An unrecognized
environment variable must not be mistaken for enabling a supported feature.

Normal fictional collection testing remains available within its existing
restrictions; use [the existing setup and manual plan](soft-collection.md).
The standard `--check-config` command validates configuration offline, but does not
attest local-only simulation isolation or authorize out-of-hours testing.

After a suitable runtime boundary is implemented, manual acceptance must include
native local success outside hours, refusal without either startup opt-in,
refusal through an actual same-machine reverse proxy and public port publication,
loopback-only media verification, fixture-tampering rejection, cross-mode state
isolation, and verification/interruption privacy checks. These tests cannot be
claimed to pass for a feature that has not been implemented.
