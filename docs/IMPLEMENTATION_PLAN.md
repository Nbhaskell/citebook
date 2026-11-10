# Implementation Plan: Citebook

Version 0.2. Phases end with an acceptance check; stop and review before the next. Names from `BACKEND_SCHEMA.md`, behavior from `TRD.md`. Windows 11 (PowerShell), local-first.

Ground rules: tests first for plain code; `FakeProvider` and `FakeKB` so tests need no model, network or GPU; small commits; decisions in `docs/DECISIONS.md`; synthetic data only.

## Phase 0: Repository and tooling

1. Repo `citebook`, structure from `TRD.md` section 3, MIT license, `.gitignore`, `.env.example`.
2. Python 3.13 environment, pinned dependencies, pytest, ruff, mypy; React + TypeScript + Vite with two entry points (widget, console).
3. FastAPI skeleton with `/api/status`; OpenAPI type generation.
4. `tools/check_env.py`: Ollama reachable, model pulled and supports tool calls, rag-project reachable. Never fails the test suite.
5. CI: lint and tests only. Pre-commit secret scan, as in the sister repos.
6. Fill in the Commands section of `CLAUDE.md`.

Acceptance: tests pass; `/api/status` answers on 8300; both front-end entry points load on 5176.

## Phase 1: Business data and calendar

1. Copy Citedesk's `universe/` without changes (record the source commit). Put every addition in `universe/citebook_extension.json`: service codes (one per priced job in `facts.json`, with `skill`, `windows_needed`, `pricing_kind`, `bookable_online`), technician skills, invented ZIP codes mapped to the universe's neighbourhoods and zones (core, outer), invented customers and bookings. A test fails if the extension contradicts `facts.json`.
2. Build `db/seed/price_book.json` and settings from `facts.json`: call-out fee $89 (credited when work is approved), emergency call-out fee $175, labour $115/hour with a 1-hour minimum, outer-area travel fee $40, flat and starting prices, office and visit hours, closed dates, 2-hour arrival windows, `free_cancel_hours` 24. Citebook-only settings: `hold_ttl_business_hours`, `quote_ttl_minutes`, `max_reschedules`, `verification_max_attempts`.
3. Make sure every policy number in the price book and settings also appears in the knowledge-base documents, with one source of truth (test that `universe/docs` and settings agree; generate text only for Citebook-only policies such as hold time).
4. Calendar seeder: four weeks of 2-hour arrival windows per technician from `visit_hours`, skipping `closed_dates`, with some pre-booked and blocked, deterministic from a seed. Seed states named (`base_week_1`, `base_week_2`) for scenarios.
5. Invented existing customers (phones 555-0100 to 555-0169 only) and bookings for verification and reschedule scenarios, some with injected text in `customer_notes` for adversarial scenarios.

Acceptance: seed is deterministic; a script prints the week's open slots per service; KB policy text and settings agree (test); extension does not contradict `facts.json` (test).

## Phase 2: Data layer and core logic (no model)

1. Migrations and enums with the sync test.
2. `money.py`, `timeutil.py` (business time zone, relative date resolution, arrival windows, office-hours arithmetic, DST tests), `pricing.py` with a test for every rule and combination.
3. Action state machine, booking and slot transitions, transactional execution with optimistic locking; concurrency test.
4. `expiry.py`: `expire_due(now)` for quotes, proposals, holds (office-hours TTL) and abandoned conversations, with fixed-time tests.
5. Verification with attempt limits and identical failure responses.
6. Screening module (reuse and adapt Citedesk's), including emergency copy.
7. Logging helper and the canary test (no message content, names, phones, addresses or amounts in logs).

Acceptance: all transitions and pricing rules tested; double-booking test passes.

## Phase 3: Tools and gate

1. Tool registry: each tool's kind, requirements, Pydantic arguments, model-facing description.
2. Gate with every check in `TRD.md` section 5, tested one by one, plus tests for malicious calls: other customer's reference, unknown tool, extra arguments, ids from another conversation, expired quote.
3. `KnowledgeBase` interface, `RagProjectKB`, `FakeKB`; `get_policy` with relevance threshold. `RagProjectKB` needs rag-project's retrieval-only `POST /api/search` (TRD 6.1). That endpoint is a separate change in the rag-project repo; until it exists, `get_policy` runs on `FakeKB` and the integration test is skipped.
4. Outbox drafts from fixed templates.

Acceptance: the full booking, reschedule, cancellation and refund flows run through tools and the gate in tests with no model.

## Phase 4: Turn loop and model

1. `ModelProvider`, `OllamaProvider` (tool calling), `FakeProvider`; recorded-response contract test.
2. System prompt and tool descriptions, versioned (`prompt-v1`).
3. Turn loop with limits, refusals with `say` hints, timeouts and fallback.
4. Reply checks with regeneration; number-words and time parsing; tests with paraphrases.
5. Confirmation endpoints with payload hash binding.
6. Manual smoke test: ten hand-written conversations against the real local model; note failures in `docs/DECISIONS.md`.

Acceptance: a real local-model conversation books a hold end to end; typed "confirm" never executes; reply checks block a planted wrong price.

## Phase 5: API, widget and console

1. API routes from `TRD.md` section 11 with session tokens.
2. Widget: launcher, messages, slot chips, quote block, confirmation card states, receipts, emergency block, handoff.
3. Console: queue, conversation detail with activity timeline, decisions, calendar, outbox, status.
4. Demo website page.
5. Accessibility pass and 360 px pass.

Acceptance: a person new to it can book a hold in the widget and approve it in the console without explanation; keyboard-only run works.

## Phase 6: Scenarios and evaluation

Use `qa-eval-harness`.

1. Write about 60 dev scenarios across families first (about 40 benign, 20 adversarial). Keep a list of families reserved for sealed.
2. Runner through the real API; button presses; 3 repeats per scenario; run metadata.
3. Scorer: end state, forbidden outcomes, escalation, attack goals, reply-check failures reaching the customer, turns, latency.
4. Dev runs and fixes. Ablations: each `CITEBOOK_ABLATION` value (TRD section 9), thinking on versus off.
5. Write the sealed scenarios (about 40, at least a third from families with no dev scenarios, about half adversarial) after dev numbers are frozen. Run once (3 repeats). Report as it comes out, with "new family" and "known family" results shown separately.
6. Error analysis with transcript excerpts for every violation and successful attack.
7. Report in `eval/reports/` with limits. Compare the attack results with rag-project's documented injection result: 32 of 45 attempts resisted over 5 repeats, which is about 29% attack success. Citebook reports attack success, so state the direction of both numbers and that the tests are different.

Acceptance: report complete; README numbers match the report exactly.

## Phase 7: Hardening

1. Fuzz the message endpoint and tool arguments; long inputs, unicode tricks, nested JSON in messages.
2. Failure drills: model down mid-turn, KB down, database locked, slot race under load.
3. Dependency and license audit; secret scan on full history.
4. Confirm no network clients beyond model and KB (test).

Acceptance: drills behave as documented; no high-severity findings open.

## Phase 8: Documentation and publish

Use `project-case-study-readme` and `pre-publish-checklist`. README with the problem, the principle, a real screen recording of booking and of a blocked attack, the evaluation tables (with and without guards), limits, and local setup (Windows first). Link the sister projects and explain the shared fictional business.

## Stretch (after v1)

1. `HostedProvider` with cost per booking and a side-by-side report; live demo only when it helps a specific application.
2. Read-only calendar sync (for example Google Calendar) for availability.
3. Deposit flow using a payment provider's test mode, with staff-only refunds still.
4. Staff login and customer sessions over HTTPS (required before any network exposure).
5. Daily brief feed: held bookings, expired holds, handoffs for the capstone project.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Local model's tool calling unreliable | Medium | Fewer tools per state, `say` hints, measure per family, hosted comparison later |
| Reply checks too strict on natural language | Medium | Dev false-alarm measurement; conservative but tuned on dev only |
| Date and time bugs | Medium | Code-owned resolution, exact dates on cards, DST and boundary tests |
| Scenario overfitting | High | Sealed families written late and run once |
| Scope creep into payments or messaging | Medium | Non-goals; outbox drafts only |
| Universe data drifts from Citedesk | Low | Copy once without changes, record the commit, keep additions in the extension file, test that they agree |
| rag-project retrieval endpoint not available | Medium | `FakeKB` until it lands; the endpoint is a small, separate rag-project change |

## Definition of done (v1)

MVP requirements implemented and tested; no path executes refunds or sends messages; booking changes only by button press; evaluation report with sealed results and baseline comparison; hardening and pre-publish checks complete; README matches the report.

## First prompt for Claude Code

> Read `CLAUDE.md`, then the documents in `docs/`. Do Phase 0 only from `docs/IMPLEMENTATION_PLAN.md`. Ask me before installing anything outside the project. When Phase 0's acceptance check passes, fill in the Commands section of `CLAUDE.md`, summarize what you did, and stop.
