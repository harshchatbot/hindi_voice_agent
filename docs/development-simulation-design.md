# Separate development simulation worker — design decision

> Browser/WebRTC review remains applicable. A separate native microphone/speaker
> runner is now available; see [native simulation](native-collection-simulation.md).
> It has no incoming signaling/media listener and does not implement the browser
> mode proposed below or change production admission.

Status: **browser design proposed, blocked on runtime containment; not implemented**.
Reviewed against the installed Pipecat 1.12.0 transport and locked aiortc/aioice
sources on 19 October 2026. This design supplements the
[security review](local-simulation-security-review.md). No production admission
change, simulation flag, runnable simulation command or dependency change is made.

## Smallest application design

Use a separate `techfi-collection-sim` executable and development module, with its
own app factory. Do not add a simulation branch to `server.create_app`,
`DemoCallingPolicy`, or the existing offer endpoint. The executable name here is
proposed, **not registered**.

The development configuration would accept only credentials, approved provider
presets, persona, bounded session timeouts and explicit simulation startup consent.
It would reject collection record input from environment variables, JSON, files,
URLs and browser requests. Reuse Pydantic secret handling; never copy credentials
into fixtures, output, browser configuration or diagnostics. Existing credentials
may be used only after an operator explicitly starts a manual conversation.

Keep one immutable Python fixture catalog in the development module, for example
`fictional-emi-001`: Demo Prerna Bank, Demo Asha Verma, INR 4500.00,
2026-10-25, personal loan, due, official app and bank branch. Accept only its ID,
approved provider selections and persona. Reject an accompanying customer record,
unknown fields or unknown fixture IDs. The date is deliberately fixed fictional
data, not the current date or a customer-data lookup.

Create a fresh workflow, context and session scope per conversation. Keep
suppression and outcomes exclusively in the development process, keyed by fixture
ID; never import, inject or reference production admission/storage instances.
Suppression persists until that development process is explicitly restarted;
absence of calling-hour/retry scheduling is not permission to ignore an opt-out.
There is no customer-data/action service registry: only existing STT, LLM and TTS
adapters may be constructed. Requests for human help or payments produce structured
reports only; they cannot invoke a payment, CRM, telephony or customer-data action.

Reuse `CollectionWorkflow`, collection frame processors, transcript fences,
interruption handling, persona presets and latency observers. Preserve exact
finalized demo verification before loan disclosure and confirmation before
recording proposals. Keep the public demo challenge explicitly labeled as a test,
never real identity verification. Keep results in memory and out of logs.

`build_session` currently checks `collection_policy.within_hours()` before creating
providers and during response handling; `run_session` also enforces the remaining
calling window. Do **not** pass an always-allowed fake policy or set extended hours.
If containment becomes available, extract only the common pipeline assembly into
an internal helper that receives an already-created workflow and session controls.
The existing production wrapper must retain every admission and ongoing window
check. The separate development wrapper would construct its own workflow and
bounded lifecycle. This avoids making simulation a runtime option of the normal
worker while preserving the existing collection safety processors.

The development page would show a persistent **SIMULATION — FICTIONAL DATA**
banner, immutable fixture details and locked provider/persona selectors during a
session. Sarvam Saaras v4, Sarvam Conversations and Bulbul presets would use the
existing adapters, including their documented experimental limitations. No provider
calls occur on page load, config validation or offline checks.

## Runtime feasibility: current browser transport is blocked

The separate process and fixture catalog provide data/state separation. They do
not establish a local-only browser/media boundary.

| Surface | Proposed control | Remaining limitation |
| --- | --- | --- |
| Signaling | Bind exactly `127.0.0.1`, separate port, `proxy_headers=False`, no forwarded-IP authorization, same-origin checks | A local reverse proxy or tunnel can still connect to the listener. Neither a startup flag nor a secret browser token proves network provenance. |
| Server WebRTC | No STUN/TURN; restrict interfaces before socket creation if supported | `SmallWebRTCConnection` and aiortc `RTCConfiguration` have no public loopback/interface allowlist. aioice explicitly omits loopback addresses and gathers other eligible interfaces. |
| Browser WebRTC | Empty ICE servers; restrictive supported browser policy if available | JavaScript has no portable loopback-only candidate gathering setting. Relay-only would require TURN and would not supply local-only isolation. |

Filtering SDP after gathering, removing `onicecandidate` messages or inspecting a
selected candidate after connection is insufficient: sockets/candidates already
exist and connectivity checks may already have occurred. Do not patch private
aiortc state or globally monkeypatch aioice enumeration to present this as a
supported local transport. HTTPS, CORS and loopback binding alone also do not
prove containment.

Consequently the current stack cannot meet the requested verified boundary.
An entry point that merely rejects every session would not provide the requested
manual testing capability; none is added. The production worker is unchanged.

## Practical routes to unblock

The smallest alternative worth a separate evaluation is a **native local-audio
development runner**, using host microphone/speaker instead of browser WebRTC.
It could reuse the same pipeline and fixed fixtures with CLI persona selection,
and avoid incoming signaling/media listeners entirely. This changes the browser
test experience and does not satisfy a requirement for browser signaling; it is
an alternative design, not an implemented or verified solution. Check optional
audio dependencies, supported transport lifecycle and device ownership first.
Remote-desktop/virtual-audio access remains a host security responsibility.

To retain the browser experience and the stricter proxy/public-container refusal
requirement, first supply a reviewed runtime containment mechanism covering the
browser, worker and media sockets. Its launcher must verify enforcement before
listening or creating providers, deny unapproved inbound paths, and allow only
the required outbound provider access. A generic container or a network namespace
alone is not proof: published ports, same-namespace proxies and browser access
must be addressed. No such launcher or enforceable policy is supplied by this
repository. Adding privileged OS networking/desktop infrastructure is larger than
this focused worker change.

## Offline verification and eventual acceptance

Existing offline checks, runnable from the repository root:

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
node --check apps/voice-worker/src/techfi_voice/static/client.js
node --test tests/test_playground_client.cjs
git diff --check
```

These verify existing behavior; they do **not** attest runtime isolation or test a
simulation worker that does not exist. No paid calls or live audio are needed.

Before registering the proposed executable, add offline tests for fixture-only
request schemas, rejected environment/file record injection, isolated stores,
suppression, unverified disclosure, stale/interrupted frames and structured
outcomes. Run the same pipeline tests through both wrappers. Normal production
admission must still reject outside 08:00–19:00 IST, and production runtime must
still stop at the window end. Simulated midnight success must never require a
modified production policy.

The containment launcher additionally needs real local, non-provider acceptance
checks: inspect listening/gathered sockets, attempt remote/proxy/container paths,
verify rejection before provider creation, and demonstrate refusal if enforcement
is missing or removed. Mocked HTTP peer metadata cannot establish these facts.
Only after those checks pass should startup commands and explicitly initiated
manual microphone tests be documented. There is currently **no supported any-hour browser
simulation startup command**. Do not widen production hours, alter the clock or
set an unregistered environment flag to work around admission.
