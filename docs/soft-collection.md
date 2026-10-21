# Soft Collection Agent — fictional local demo

This extends the existing Pipecat worker; no new service, database, telephony,
Excel ingestion, provider migration or frontend framework is introduced. General
Assistant remains the default. Select **Soft Collection Agent** for the explicitly
fictional workflow. No real customer verification procedure was supplied, so
real collection activity is unsupported and `fictional=false` is rejected.

The functional reference is Sarvam's
[collection-agent cookbook](https://docs.sarvam.ai/api/cookbook/example-voice-agents/collection-agent).
Its LiveKit implementation is not imported. Existing Sarvam Realtime Saaras v4,
OpenAI/Sarvam LLM and Sarvam/Google TTS adapters remain in place.

## Workflow and disclosure boundary

Normal path:

`GREETING → IDENTITY_VERIFICATION → PAYMENT_DISCUSSION → OUTCOME_CONFIRMATION → CLOSING`

The greeting uses the server-configured assistant name, configured fictional bank,
automated AI disclosure and selected Hindi grammatical persona. It does not name
the customer, loan type, amount, date or status. The public test phrase is
**“neela kamal” / “नीला कमल”**. Only an exact finalized phrase can establish
**demo_verified**; a name, “yes”, partial transcript or LLM assertion cannot.
This phrase is a public test fixture, not a secret, an approved bank procedure,
real identity proof or production authorization. Two failed attempts close the
session without disclosing loan data. Declining a convenient time closes politely.

After demo verification, application-owned templates read the supplied EMI
amount (INR), due date, status, loan type and selected approved payment methods
exactly. No account numbers, phone numbers or payment URLs are supported.
Customers can report disputes, already-paid claims or hardship, request a plan,
callback or authorized human assistance. These reports can also be recorded
without verification, without confirming loan existence or revealing internal
facts; the outcome retains `not_verified`. A promise date requires demo verification.

Wrong recipients, failed verification and opt-outs close through separate
outcomes. A wrong-recipient report immediately revokes demo verification.
Requests and promises use **OUTCOME_CONFIRMATION**; only an explicit “haan” /
“yes” confirms the proposal. “No” discards it. An interrupt, playback completion,
model output, timeout or disconnect never supplies customer confirmation.
An explicit human/callback request can replace a pending proposal.

The existing LLM still receives one streamed request per accepted user turn. For
rule-controlled turns its input is a neutral marker; it receives neither the
demo challenge nor the configured customer/loan facts. For other verified questions
it chooses one of `overview`, `methods`, `status`, `unsupported`. The response
processor buffers at most 64 label characters and **never forwards model prose**
to TTS. Unknown output maps to a safe refusal. The LLM has no authority to verify
identity, set financial values, approve plans, invent links or update outcomes.
All spoken payment text comes from validated server templates.

This deliberately limits conversational flexibility. Relative/ambiguous dates
such as “kal”, “tomorrow” and `26/10/2026` are not inferred. Dates can be supplied
as `2026-10-26`, `26 October 2026` or `26 अक्टूबर 2026`, with an explicit year and
numeric day, and must be confirmed. Number-word date recognition and broad
Hindi/Hinglish intent coverage still require manual evaluation. The supplied
record is never modified, including when a customer says they already paid.
A record already marked paid does not solicit a new payment promise.

## Playground and personas

Demo fields are prepopulated with invented data: Demo Prerna Bank, Demo Asha
Verma, INR 4500.00, 2026-10-25, personal loan, due, official app and branch.
Names must begin `Demo `, use permitted name characters and stay within bounds.
This format does not detect whether a person has disguised real data: operators
must keep all inputs fictional. Amounts, dates, statuses, loan types and payment
methods are validated; unknown fields, URLs and account identifiers are rejected.
All fields and provider/persona choices lock when Start is pressed. The backend
revalidates an immutable case and creates an independent settings/workflow snapshot.
It does not accept model IDs, voice IDs, calling hours, retries or policy enablement
from the browser. No session update endpoint is added.

| Persona | Assistant default | Bulbul v3 | Bulbul v4 Flash | Google Chirp 3 HD |
| --- | --- | --- | --- | --- |
| Male | Aarav | Shubh | Shubh Hindi Customer | Achird |
| Female | Asha | Ritu | Ritu Hindi Customer | Kore |

Preset voices remain server-configured. Gender/catalog mismatches fail closed.
The supported catalog is deliberately bounded; custom voices need an explicitly
reviewed gender mapping. Existing v3/v4/Google models and GPT-4.1 Mini/Sarvam 105B
Conversations choices remain available. GPT-4.1 Mini, v3/Shubh and male persona
remain defaults. v4 and Sarvam LLM retain the limitations in
[the experimental provider guide](sarvam-experimental.md).

## Calling policy and compliance limits

The server admits collection demos only when `TECHFI_COLLECTION_DEMO_ENABLED=true`
and within **08:00 inclusive–19:00 exclusive Asia/Kolkata**, per the supplied
window. Configuration can narrow but cannot widen that outer window. Admission
precedes peer/provider creation. Failed connection attempts conservatively consume
an attempt. The default is **one attempt per India calendar day**, a configurable
retry gap (default one hour), and one active collection demo per tenant. A server
instance treats all edited cases for a tenant as **one fictional recipient**, so
changing browser names cannot bypass admission, retries or suppression.

The worker bounds duration by the calling-window deadline, rechecks it while
running, checks it before greeting/releasing replies and cancels at expiry. An
explicit do-not-call request or wrong-recipient outcome suppresses further demo
admissions for that tenant in the same process. “Stop”, “bye” or End alone is not
classified as a permanent DND request. No repeat contact is scheduled.

The [RBI circular of 12 August 2022](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12378)
requires regulated entities to prevent intimidation, harassment, misleading
representations and persistent recovery calls; it also specifies the 8 a.m.–7 p.m.
outer calling window for the covered overdue-loan recovery activity. Responsibility
remains with the regulated entity. It supplements other directions and explicitly
excludes certain microfinance loans; this demo excludes microfinance loan types.
**The one-attempt limit is our conservative demo policy, not an RBI-prescribed
numeric retry limit.** Bank counsel/policy owners must approve applicable scripts,
consent/DND handling, verification, retry limits and recovery rules before any real
use. This implementation is not a regulatory-compliance certification.

**Admission and DND are process-local and reset on restart.** They do not provide
multi-worker enforcement, durable DND/consent, real-recipient retry accounting,
bank authorization or a production audit trail. Real bank use must remain disabled
until those durable controls and an approved verifier exist. Restarting is suitable
only for independent fictional manual fixtures, not evading real retry/DND limits.

## Outcomes and privacy

`VoiceSession.result()` adds a typed `collection` object with:

- call outcome, workflow state, `not_verified` / `demo_verified`;
- customer-reported promise-to-pay date and callback request/date;
- dispute, already-paid, hardship, repayment-plan and human-escalation requests;
- explicit do-not-call request and pending-confirmation status;
- fixed `demo_only=true` and `financial_records_modified=false`.

Transport completion/cancellation/failure remains a separate outcome. A confirmed
promise is a **customer report**, never a payment confirmation, settlement, approved
plan or financial record update. No live transfer, bank notification or callback
scheduling is performed or claimed. Unconfirmed proposals are marked pending and
never silently committed. Requests are local only; the assistant directs customers
to the bank's independently verified official channels for actual assistance.

The playground retrieves a completed result using the opaque peer ID and the
existing same-origin POST guard. At most 16 outcomes are retained in memory and
expire after ten minutes; old entries are evicted and nothing is persisted. The
result contains no names, EMI amount, account identifiers, raw audio or transcripts.
Dates and request flags are still sensitive: keep the loopback playground private.
It is not public authentication or a production record-access system.
Provider/debug payloads and outcomes are not added to logs. Conversation context
is cleared during teardown. No account identifiers, credentials or customer data
are embedded in latency events.

## Configuration and startup

Keep existing keys in the ignored `.env`; do not overwrite it. New optional
server settings are listed in `.env.example` with placeholders only:

| Setting | Default |
| --- | --- |
| `TECHFI_COLLECTION_DEMO_ENABLED` | `false` |
| `TECHFI_COLLECTION_START_HOUR` / `TECHFI_COLLECTION_END_HOUR` | `8` / `19` |
| `TECHFI_COLLECTION_MAX_ATTEMPTS_PER_DAY` | `1` |
| `TECHFI_COLLECTION_RETRY_GAP_SECONDS` | `3600` |
| `TECHFI_ASSISTANT_MALE_NAME` / `TECHFI_ASSISTANT_FEMALE_NAME` | `Aarav` / `Asha` |
| `TECHFI_PLAYGROUND_SARVAM_FEMALE_VOICE_ID` | `ritu` |
| `TECHFI_PLAYGROUND_SARVAM_V4_FEMALE_VOICE_ID` | `ritu_hi_customer` |
| `TECHFI_PLAYGROUND_GOOGLE_FEMALE_VOICE_ID` | `hi-IN-Chirp3-HD-Kore` |

For configuration-only validation (no provider calls):

```sh
TECHFI_COLLECTION_DEMO_ENABLED=true \
TECHFI_STT_PROVIDER=sarvam-realtime TECHFI_STT_MODEL=saaras:v4 \
TECHFI_LLM_PROVIDER=openai TECHFI_LLM_MODEL=gpt-4.1-mini \
TECHFI_TTS_PROVIDER=sarvam TECHFI_TTS_MODEL=bulbul:v3 TECHFI_TTS_VOICE_ID=shubh \
uv run --no-sync techfi-voice --check-config
```

This does not authenticate providers or validate a particular offer. For an
explicitly authorized, paid manual session, repeat without `--check-config`,
open `http://127.0.0.1:7860` and select Soft Collection Agent. Test only within the
calling window. The existing optional Google dependency/ADC setup is unchanged.

## Manual test plan (not executed)

Use headphones and fictional fixtures. The following live tests incur provider
charges; none were run for this iteration.

1. Confirm General Assistant/default providers still work. Select collection,
   change demo fields, choose male/female and verify matching voice/name/grammar.
   Start; verify every setting locks. End; controls unlock and an outcome appears.
2. Before verification, ask for amount/date or give only a name/“yes”. Verify no
   loan facts are spoken. Two failures must close. Try “wrong number” and an
   explicit do-not-call request; verify their distinct outcomes and suppression.
3. In an independent fictional fixture, say “neela kamal”. Verify the response
   clearly says demo verification only, then reads the exact configured values and
   approved methods. Ask for a penalty, discount, payment link or extension; none
   should be invented. Test a record marked paid without a new payment demand.
4. Request a plan, report hardship, dispute a record, claim already paid, request
   a callback or human. Confirm each report with “haan”; verify the correct flags
   and no claim of executed transfer, callback, approval or updated payment status.
5. Say “I will pay 26 October 2026” (use a future date within a year). Verify the
   date is read back and remains absent from confirmed outcomes until “haan”.
   Try “kal”, ambiguous numeric dates, two dates and “no”; nothing is auto-inferred.
6. Interrupt before the LLM label finishes and during TTS. Verify no old facts or
   stale response leaks into the next turn. Interrupt a pending confirmation;
   interruption alone must not confirm it. Repeat with “wrong number”/opt-out.
7. Check two isolated sessions/history in separate fictional tenants. Within one
   tenant, a second active call and retry-limit violations must be blocked. Check
   outside-window admission and expiry at closing time using an offline clock
   fixture rather than weakening production bounds. Verify no sensitive logs.
8. Compare Sarvam/OpenAI LLM and each approved TTS/persona. Inspect existing timing
   events: LLM first token now measures the **intent label**, and collection TTS
   intentionally waits for the short label to finish before releasing trusted
   text. No extra provider request is introduced. General-agent streaming remains
   unchanged; see [latency metrics](latency.md).

Offline coverage includes domain boundaries, imports, identity, report confirmation,
calendar dates, personas, retries/DND, cancellation races, mocked running Pipecat
pipelines and privacy-safe timing, server routes and browser state. It does not
establish live recognition accuracy, collection-policy approval, accessibility,
visual rendering, provider availability or microphone-to-speaker functionality.

## Changed files in this iteration

| Files | Responsibility |
| --- | --- |
| `apps/voice-worker/src/techfi_voice/collection.py` (new) | Validated fictional case, state machine, reports and server calling policy |
| `apps/voice-worker/src/techfi_voice/collection_pipeline.py` (new) | Redacted per-turn LLM context and trusted-response gate |
| `apps/voice-worker/src/techfi_voice/config.py` | Agent/persona/policy configuration and validation |
| `apps/voice-worker/src/techfi_voice/prompt.py` | Collection intent-only prompt and female general-agent grammar |
| `apps/voice-worker/src/techfi_voice/policy.py` | Pass stop text to the owned callback; distinguish DND from ending a call |
| `apps/voice-worker/src/techfi_voice/session.py` | Per-session workflow, gates, disclosure/closing controls and typed result |
| `apps/voice-worker/src/techfi_voice/runtime.py` | Calling-window deadline and cleanup of pending response state |
| `apps/voice-worker/src/techfi_voice/server.py` | Offer validation, policy admission, collection greeting and bounded outcomes |
| `apps/voice-worker/src/techfi_voice/integrations/providers.py` | Server-owned male/female voice selection and use-case prompt routing |
| `apps/voice-worker/src/techfi_voice/static/index.html`, `client.js`, `styles.css` | Agent/persona controls, fictional fields, locking and local outcome display |
| `tests/test_collection.py` (new), `tests/test_playground_client.cjs` | Domain, pipeline, policy, privacy, route and browser tests |
| `.env.example`, `README.md`, `docs/playground.md`, `docs/soft-collection.md` (new) | Placeholder configuration, boundaries and manual acceptance plan |

Pre-existing uncommitted provider/latency work was preserved. No credentials,
lockfile, dependencies, frontend framework, control-plane API or legacy code were
changed for this iteration.

Validation: **177 Python tests passed**, one optional local socket integration test
excluded; **10 mocked Node tests passed**. Ruff lint/format, JavaScript syntax and
`git diff --check` passed. Fifteen warning occurrences originate upstream
(`audioop`, Pipecat AudioContextTTSService declaration and Silero resource loading).
No paid provider calls, live audio tests, commits or pushes were performed.
