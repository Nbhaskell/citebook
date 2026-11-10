# Product Requirements Document: Citebook

Name: Citebook (repo `citebook`)

Version 0.2 (2026-11-09: review decisions in `docs/DECISIONS.md`). Names of statuses, tools, decisions and check codes are defined in `BACKEND_SCHEMA.md`.

## 1. Summary

Citebook is a website chatbot for a small service business that can **do things**, not only answer. It checks availability, quotes prices, and places booking holds through tool calls. Prices and policy are computed in code, every action is confirmed by the customer through a button that code renders, bookings are confirmed by staff, and refunds are never automatic.

It is the sixth project in a suite: `rag-project` (answers from documents), `citewise-markets` (checked research answers), `citecheck` (evaluation of AI answers), `citedesk` (inquiry review desk), `citetally` (invoice review desk). Citebook reuses the invented plumbing company from Citedesk (Kestrel Hollow Plumbing), with its prices and policies unchanged, so the suite tells one story about one business.

## 2. Problem

Small service businesses lose bookings out of hours and spend staff time answering "are you free Thursday?" and "how much for a blocked drain?". A chatbot that answers is helpful; one that books is far more valuable, but also far riskier. If a bot can act, a wrong price, a booking for the wrong person, or an attacker talking it into a refund costs real money and trust. Most chatbot demos skip that risk.

Citebook's point is to show that an acting chatbot can be useful **and** safe: the model talks and chooses tools; code decides what is allowed, computes every number, and checks every reply.

## 3. Users

| Persona | Needs |
|---|---|
| Customer (website visitor) | Quick answers, a price they can trust, a slot that fits, a clear record of what was agreed, a person when needed |
| Office staff (dispatcher) | Fewer routine calls, a short queue of holds to confirm and requests to decide, full context for each |
| Owner | More bookings out of hours, no surprises on price or refunds, confidence the bot cannot be talked into things |

## 4. Assumptions

- Local-first: a local model via Ollama with tool calling; a hosted model is an option designed in and added later.
- All data synthetic; the business, staff, customers and calendar are invented. Citedesk's `universe/` is copied unchanged; Citebook's additions (ZIP code map, technician skills, customers, bookings) live in a separate extension file.
- One business, one time zone, English, USD.
- No real payments, no real messages sent. Notifications are drafted to an outbox table and shown in the staff console.
- Single staff user in v1, no login (local only), same as the sister projects.

## 5. Goals

1. A customer can get a correct price, find an open slot, and place a booking hold in one conversation.
2. A verified customer can view, reschedule and cancel their own booking. Changes with less than 24 hours' notice go to staff, who review the late fee.
3. Every price, time, date and booking reference the bot states comes from a tool result in that conversation.
4. No booking changes without the customer pressing Confirm on a card rendered by code.
5. Holds, late cancellations, refunds and exceptions go to staff. Refunds are never executed by Citebook.
6. The bot cannot see or change another customer's booking.
7. Prompt-injection resistance is measured with a published attack suite and a before/after comparison against an unguarded baseline.
8. Results are reported per scenario family with the limits stated, following `qa-eval-harness` and citecheck-style reporting.

## 6. Non-goals (v1)

- Taking payments or deposits, or issuing refunds.
- Sending email, SMS or any message. Drafts only.
- Real calendar integration (Google Calendar and similar are later options, read-only first).
- Technician routing and travel-time optimization.
- Voice, multiple languages, multiple businesses.
- Diagnosing plumbing problems beyond the business's own documents.
- Authentication for staff (v1 is local only, bound to `127.0.0.1`).

## 7. Success metrics (hypotheses until measured)

| Metric | Target | How measured |
|---|---|---|
| Task success on benign scenarios | at least 0.85 | End state equals `expected_end_state` |
| Policy violations on any scenario | 0 critical; report every one | Any `forbidden` outcome, unconfirmed change, other-customer data shown, refund executed |
| Attack success rate (adversarial scenarios) | at most 0.05, with every success described | Attack's stated goal achieved in end state or reply |
| Unguarded baseline attack success | reported, no target | Same scenarios, gate and reply checks disabled |
| Stated values not from a tool | 0 in replies shown to customers | `PRICE_FROM_TOOL`, `TIME_FROM_TOOL`, `REFERENCE_FROM_TOOL` after the retry |
| Correct escalation | recall at least 0.90 on scenarios that must escalate | `expected_escalation` |
| Unnecessary escalation | at most 0.15 on benign scenarios | Escalation where none expected |
| Turns to complete a booking | report median | Scenario transcripts |
| Time per assistant turn | report median and 90th percentile | Logs |

## 8. User stories

1. As a customer, I ask how much it costs to clear a kitchen drain on Saturday and get a price broken into lines, labelled as fixed or estimate.
2. As a customer, I ask for the next slot on Thursday afternoon and see real open times.
3. As a customer, I confirm a slot and price on a card, and get a booking reference that says "held until our team confirms".
4. As a customer, I give my booking reference and phone's last four digits and move my appointment. With less than 24 hours' notice, the move goes to staff and I'm told a late fee may apply.
5. As a customer, I cancel 24 hours or more ahead and it's done; with less than 24 hours' notice, my request goes to staff and I'm told so.
6. As a customer, I ask for a refund and I'm told a person will review it; nobody promises me money.
7. As a customer who smells gas or has water flooding, I get safety instructions and the emergency line straight away, not a booking flow.
8. As staff, I see held bookings, late cancellations, refund requests and handoffs in one queue, with the transcript and every tool call.
9. As the owner, I can read a report showing how often attacks succeeded with and without the guards.

## 9. Functional requirements

Priorities: M = must for MVP, S = should, C = could.

**Conversation**
- FR-1 (M) Embeddable chat widget on a demo website page for the invented plumber.
- FR-2 (M) Screening in code before the model: emergency keywords, sensitive numbers masked, suspicious-input patterns, abuse, spam (reuse Citedesk's screening approach).
- FR-3 (M) Emergencies short-circuit to fixed safety copy and the emergency line, and create a `handoff`.
- FR-4 (M) The model answers general questions only from `get_policy` passages; if nothing relevant is found it says so and offers a person (`NOT_IN_KNOWLEDGE_BASE`).
- FR-5 (S) Conversation resumes after a page refresh within the same browser session.

**Tools and gate**
- FR-6 (M) The model can call only the tools in `BACKEND_SCHEMA.md` section 2.1. Unknown tools are refused.
- FR-7 (M) Every tool call passes through a policy gate in code that checks state, verification, contact details, argument validity, rate limits and policy, and returns a `gate_decision`.
- FR-8 (M) Arguments are validated against schemas; dates are resolved by code from the customer's words where possible ("Thursday afternoon") and echoed back for confirmation.
- FR-9 (M) Prices come only from `quote_price`; quotes expire.
- FR-10 (M) `find_booking` and every change to an existing booking require `verified` state, scoped to the one booking that was verified.
- FR-11 (M) Verification failures are counted; after the limit the conversation escalates with `VERIFICATION_FAILED`.

**Actions and confirmation**
- FR-12 (M) `propose_*` and `request_*` tools create actions with a summary built by code from fixed templates.
- FR-13 (M) The widget shows a confirmation card with that summary and Confirm and Cancel buttons. Only a button press confirms; typed "yes" does not.
- FR-14 (M) Confirmation binds to the action's payload hash; any change requires a new proposal.
- FR-15 (M) Execution is transactional and handles races (slot taken meanwhile) without partial changes.
- FR-16 (M) Holds expire after a configured number of office hours if staff do not confirm; the slot reopens. Time outside office hours does not count, so a hold placed at night survives until staff can see it.
- FR-17 (M) Refund requests and policy exceptions are only ever recorded for staff.

**Replies**
- FR-18 (M) Every assistant reply passes code checks (`reply_check_code`). A failed check triggers one regeneration with feedback; a second failure sends a safe fallback and escalates with `REPLY_CHECK_FAILED`.
- FR-19 (M) Replies are rendered as text, never HTML.

**Staff console**
- FR-20 (M) Queue of `pending_staff` actions and handoffs, with transcript, tool calls, gate decisions and reply checks.
- FR-21 (M) Approve and reject (with reason); take over a conversation; close.
- FR-22 (M) Calendar view of slots and bookings by status.
- FR-23 (S) Outbox view of drafted notifications (never sent).
- FR-24 (S) Settings: hold time, cancellation window, reschedule limit, service ZIP codes and zones, price book version (read-only view of the price book).

**Evaluation**
- FR-25 (M) Scenario runner that drives conversations through the same API as the widget, presses buttons, and records transcripts, tool calls and end state.
- FR-26 (M) At least 100 scenarios: about 60 benign and 40 adversarial, split by family into `dev` and `sealed`.
- FR-27 (M) Scorer separate from the runner; reports task success, violations, attack success by category, escalation metrics, turns and latency.
- FR-28 (M) Baseline mode: same model and tools with the gate's policy checks and reply checks disabled, plus one switch per removable layer (`CITEBOOK_ABLATION`, TRD section 9). For measurement only, never selectable in the UI, refused by the API outside the eval runner.
- FR-29 (S) Optional simulated customer (a second model playing the persona) for free-form variations, kept separate from scripted results.

**Platform**
- FR-30 (M) `ModelProvider` interface with Ollama first; hosted provider later.
- FR-31 (M) Synthetic-data label in the widget and console.
- FR-32 (M) No message content in logs or audit events.

## 10. Non-functional requirements

- Local, single machine (Windows 11 primary), API on `127.0.0.1:8300`, web on `127.0.0.1:5176`.
- Assistant turn under 15 seconds median on the reference machine with an 8B model; report the real number and the machine's specifications.
- Test suite runs with no model, network or GPU (`FakeProvider`, `FakeKB`).
- Money never in floats; times in UTC storage, business time zone display.
- MIT license; no copyleft dependencies.

## 11. Phasing

- MVP: FR-1–4, 6–22, 25–28, 30–32.
- v1.1: FR-5, 23, 24, 29.
- Later: hosted provider and live demo, read-only calendar sync, deposits via a payment provider's test mode, staff login.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Local 8B model makes poor tool choices | Small tool set, clear descriptions, gate refusals with `say` hints, measured by scenario family |
| Prompt injection through chat, notes or documents | Gate in code, confirmation by button, untrusted-field marking, reply checks, adversarial suite with baseline comparison |
| Bot states a price or time not from a tool | Reply checks with regeneration and fallback |
| Double booking under concurrency | Optimistic locking on slots, transactional execution, tests |
| Date and time confusion ("next Friday", time zones) | Code resolves dates; card shows the exact date and time; tests across week and month boundaries and DST |
| Overclaiming from synthetic scenarios | Sealed families, stated limits, no real-traffic claims |
| Port clashes with sister projects | 8300 and 5176 (others use 8000, 8100, 8200, 5173, 5174, 5175) |

## 13. Dependencies

Ollama with a tool-calling model (`qwen3:8b` first), rag-project for knowledge-base retrieval through a new retrieval-only endpoint (`POST /api/search`, a separate change in that repo; `FakeKB` until it exists), Citedesk's `universe/` data.

## 14. Open questions

1. Does `qwen3:8b` choose tools reliably enough, or does the turn loop need a "plan then call" step? Decide by measurement on dev scenarios.
2. ~~Should `get_policy` call rag-project over HTTP or embed a small local index?~~ Decided 2026-11-09: call rag-project's retrieval-only `POST /api/search` (D-023 in `DECISIONS.md`), with `FakeKB` for tests.
3. Which reply checks produce false alarms on natural phrasing ("around 2", "a hundred bucks")? Measure on dev.
4. Is a simulated-customer model worth the noise it adds to results? Keep it separate until shown useful.
