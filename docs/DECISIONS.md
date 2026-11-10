# Decisions

Decisions the planning documents do not cover in full. Each entry says what was chosen and why. Newest last.

## Planning

### Company data

#### D-001. Prices follow the business's published price book
Prices come from Citedesk's `universe/facts.json`. A quote shows the $89 call-out fee, the job price, and a negative line that credits the call-out fee, because the business credits that fee when the customer approves the work on the same visit. A fixed note says the call-out fee applies if the customer declines the work on the day.

#### D-002. No after-hours or weekend surcharge in online quotes
The business charges a $175 emergency call-out fee instead of the $89 fee outside scheduled-visit hours, on Sundays and on closed dates. Online slots exist only inside scheduled-visit hours, so that fee never applies to an online quote; it stays in the knowledge base for questions. Saturday is a normal visit day. `pricing_kind` has four values: `fixed`, `starting_price`, `hourly`, `quote_only`.

#### D-003. ZIP codes map to neighbourhoods and zones
Customers give a ZIP code. `zip_areas` in `universe/citebook_extension.json` maps each invented ZIP code to one of the business's neighbourhoods and a zone: `core` (no travel fee) or `outer` ($40 travel line). A ZIP code not in the map is `OUT_OF_SERVICE_AREA`.

#### D-004. A slot is one 2-hour arrival window for one technician
This matches the business's arrival windows. Each service has a `skill` and `windows_needed` (1 or 2), and availability searches only technicians with that skill. `quote_only` services cannot be booked online (a `CHECK` constraint enforces it); the assistant offers the bookable first step, such as a camera inspection.

#### D-005. Late changes go to staff
A change is late when the visit starts less than `free_cancel_hours` (24) from now. A late reschedule or late cancellation is allowed, confirmed by the customer, and sent to staff (`LATE_RESCHEDULE`, `LATE_CANCELLATION`), because the business allows late changes for a $45 fee that a person reviews. A booking that is still `held` can always be cancelled free, and code executes it. More than `max_reschedules` is `POLICY_BLOCKED`.

#### D-006. Business data is copied unchanged and extended in a separate file
`universe/` is copied from Citedesk without edits, and the source commit is recorded here when it is copied. Citebook's additions go in `universe/citebook_extension.json`; `facts.json` wins where they disagree, and a test checks that they agree. Invented customer phones use 555-0100 to 555-0169, because the business uses 555-0171 and 555-0172.

### Actions and conversations

#### D-007. Every proposed or requested action needs Confirm
Every `propose_*` and `request_*` tool creates an action that the customer must confirm on a card before anything changes or reaches staff. Only `handoff` needs no Confirm, so a customer can always reach a person.

#### D-008. Actions record their origin
`actions.origin` is `tool`, `screening` or `loop`, and `tool_call_id` is set only for `tool`. Emergency handoffs come from screening and escalations such as `REPLY_CHECK_FAILED` come from the turn loop, so neither has a tool call.

#### D-009. Handoff lifecycle
A handoff is created directly in `pending_staff` and moves to `executed` when staff take over or close the conversation. Handing off withdraws any open proposal, so no card can be confirmed while staff are in charge.

#### D-010. Hold, proposal and conversation expiry states
A hold that no one approves in time moves from `pending_staff` to `expired`, and its slot reopens. The outbox drafts `hold_expired` or `hold_declined` so staff can follow up. A conversation can become `abandoned` from `active` or `awaiting_confirmation`; any open proposal then expires.

#### D-011. One expiry function
`expire_due(now)` handles quotes, proposals, holds and abandoned conversations in one transaction. It runs at the start of each request and every 60 seconds in a background task, so the staff queue stays current when nobody is chatting. Tests pass a fixed time.

#### D-012. Hold time counts office hours only
`hold_ttl_business_hours` counts only office hours. A hold placed at night would otherwise expire before staff could see it, which would defeat out-of-hours booking.

#### D-013. A reschedule needs a new quote
`propose_reschedule(booking_reference, slot_id, quote_id)` needs a quote for the new slot. The card shows the old and new totals when they differ, and the booking keeps the new quote.

#### D-014. Verification opens one booking
Verification opens only the booking that was verified, in the gate and again in SQL, which limits what a guessed reference can reveal. A verified conversation counts as having contact details, taken from that booking's customer.

### Checks and evaluation

#### D-015. What reply checks accept
`PRICE_FROM_TOOL` accepts amounts from quotes, policy passages, settings and the price book. `TIME_FROM_TOOL` accepts dates that code wrote in the turn's state summary. Without these, correct replies that quote the $45 late fee or repeat a resolved date would fail.

#### D-016. One ablation switch
`CITEBOOK_ABLATION` has one value per removable safety layer (TRD section 9). Button confirmation and staff approval have no off switch, because switching them off would need a code path that executes on the model's word. Ownership in SQL also stays on, so the baseline understates `other_customer_data` attack success; the report says so.

#### D-017. Sealed split
At least a third of sealed scenarios come from families with no dev scenarios; the rest are new scenarios in dev families. Reports show "new family" and "known family" results separately, which measures both kinds of generalization.

#### D-018. Direction of the 32/45 comparison
rag-project's injection figure counts attempts resisted (32 of 45, about 29% attack success). Citebook reports attack success, so the report states the direction of both numbers.

### Integrations and repository

#### D-019. Knowledge base through a retrieval-only rag-project endpoint
`RagProjectKB` calls `POST /api/search`, which returns passages without a model call. `/api/ask` is not used, because it runs a full model answer (about 8 s, thinking on) on the same Ollama instance for every policy question. The endpoint is a separate rag-project change; until it exists, `FakeKB` is used. rag-project serves one documents folder at a time, so Citebook and Citedesk cannot use it at the same moment.

#### D-020. Assistant-instruction files are untracked
`CLAUDE.md` and `CLAUDE.local.md` are git-ignored, as in Citedesk, Citetally and rag-project.

## Phase 0

### D-021. Directories and sub-packages are created, empty module files are not
`docs/TRD.md` section 3 lists every module. Phase 0 creates the directories (`db/migrations`, `db/seed`, `eval/scenarios`, `eval/reports`, `tools`, `tests`, `web`) and the sub-packages (`citebook/tools`, `citebook/providers`, `citebook/prompts`). Module files such as `gate.py` and `money.py` are created in the phase that implements them, so there are no empty stubs to mislead a reader. `universe/` is created in Phase 1, when it is copied. Two folders named `tools` exist on purpose: `citebook/tools/` holds the model's tools, the top-level `tools/` holds developer scripts.

### D-022. Line endings: LF
`.gitattributes` sets `* text=auto eol=lf` so Windows checkouts and the Linux CI runner agree and ruff's formatter does not report line-ending differences.

### D-023. Python dependencies are pinned in two requirements files
`requirements.txt` holds runtime dependencies and `requirements-dev.txt` adds test and lint tools (`-r requirements.txt` plus pytest, ruff, mypy, httpx2), the same layout as Citetally. Both list every package, including transitive ones, at exact versions (a `pip freeze`), so installs are reproducible. No extra lock tool. Phase 0 installs only what Phase 0 uses. FastAPI's test client uses `httpx2` (BSD-3-Clause), which Starlette now recommends over `httpx` and which avoids `certifi` (MPL-2.0).

Type checking is `mypy --strict` with the pydantic plugin. Lint and format are `ruff` (rules `E, F, I, B, UP, SIM, RUF`, line length 100), which skips `docs/` and `web/` because recent ruff versions also format Markdown code blocks and the planning documents should not be rewritten by a formatter.

### D-024. License check of the Python dependencies
Every installed package was checked with a metadata-reading script on 2026-11-09: 32 packages, all MIT, BSD, Apache-2.0 or PSF, with one exception: `pathspec` 1.1.1 (MPL-2.0, weak file-level copyleft), pulled in by `mypy`. It is development-only, unmodified and not redistributed, so it does not affect the MIT license of this repository. Citetally accepted the same package on the same grounds. **Provisional** until confirmed for this repository. No AGPL or GPL.

### D-025. Settings use the names in TRD section 12, in one class, with `.env` optional
`citebook/config.py` has a single `Settings` class (pydantic-settings). Each field is bound to its documented variable name (`CITEBOOK_*`, plus `OLLAMA_URL`), empty values fall back to defaults so `.env.example` can ship with blank entries, and unknown variables are ignored. Phase 0 includes only the settings the status route and the safety switches need; the turn-loop limits arrive in Phase 4. Two safety switches are enforced at startup and tested: `CITEBOOK_MODEL_PROVIDER=hosted` needs `CITEBOOK_ALLOW_HOSTED=true` (rule 11), and any `CITEBOOK_ABLATION` other than `none` needs `CITEBOOK_EVAL=true` (rule 9). Service URLs must be `http://` or `https://`. Until `citebook/models.py` exists (Phase 2), the `ablation` values are a `Literal` in `config.py`; Phase 2 moves them into the shared enums.

The server's bind address is a constant, not a setting: `python -m citebook` always binds to `127.0.0.1`, because v1 has no authentication. The app is built by `create_app(settings)`, not a module-level global, so tests never read a local `.env`.

### D-026. `/api/status` reports only what exists
The status response names the service, version, provider, model, synthetic-data flag and the ablation in force. `model_reachable`, `kb_reachable` and `database` are `null` ("not checked yet") until the phases that build those parts, so the page never shows a green check for something unchecked.

### D-027. Web setup: one Vite project, two entry points, generated API types
`web/` is a hand-written Vite + React + TypeScript project (no template generator) with two pages: `/widget/` (customer widget) and `/console/` (staff console), plus a plain index page linking both in development. Shared code lives in `web/src/shared/`. The dev server runs on `127.0.0.1:5176` and proxies `/api` to `127.0.0.1:8300`, so the API needs no CORS settings. Versions are pinned exactly and `package-lock.json` is committed. TypeScript is held at 5.9.3: 7.x is outside the peer ranges of `typescript-eslint` (`<6.1`) and `openapi-typescript` (`^5.x`).

API types are generated: `python -m tools.export_openapi` writes `web/openapi.json`, `npm run gen:api` writes `web/src/api/schema.d.ts`, both are committed, and a pytest test fails when `openapi.json` no longer matches the app. Both pages show the demo-data label from `docs/UI_UX_DESIGN_BRIEF.md` and a banner when an evaluation ablation is in force. All server text is rendered as text.

### D-028. License check of the npm packages
237 packages checked on 2026-11-09. All are MIT, Apache-2.0, ISC, BSD, BlueOak or Python-2.0, with three exceptions, all build-time only and not shipped as source: `lightningcss` and its Windows binary (MPL-2.0, a Vite dependency) and `caniuse-lite` (CC-BY-4.0 browser data). Citetally accepted the same packages on the same grounds. **Provisional** until confirmed for this repository.

### D-029. Environment check reports, never installs
`tools/check_env.py` uses only the standard library, so it runs before the virtual environment exists. It reports Python 3.13, Node.js, Ollama, whether the configured model (`CITEBOOK_MODEL`, default `qwen3:8b`) is pulled and lists tool calling in `POST /api/show` capabilities, and whether rag-project answers `GET /api/status` at `CITEBOOK_KB_URL`. Failures print a Windows install or start hint. It never installs anything and exits 0 unless `--strict`, so it cannot fail the test suite. All machine access goes through an injected `Probes` object, so its tests need no network or binaries. The retrieval-only endpoint (D-019) is checked once it exists.

### D-030. Personal-information guard on every commit and in CI
`tools/check_personal_info.py` runs from `.githooks/pre-commit` (staged files) and `.githooks/commit-msg` (the message), and with `--all` in CI. It flags local user-profile paths, email addresses and phone numbers that are not invented ones, notes that describe the author, tracked assistant-instruction files, a commit email that is not a GitHub noreply address, and, in Markdown and commit messages, wording that names a person as the actor or uses first person, plus assistant attribution lines. Findings show file, line and label, never the matched text. Enable once per clone with `git config core.hooksPath .githooks`. Both hooks refused test commits on 2026-11-09: a staged file with an email and a local path, a message with an attribution line, and a first-person message.

The guard does not catch everything: names of real people, or personal details written in plain words, are left to review.

### D-031. Secret scan with a pinned, checksum-verified gitleaks
`tools/secret_scan.py` downloads gitleaks 8.30.1 (the version Citetally uses) into the git-ignored `.tools/` folder and checks it against SHA-256 values pinned in the script, so nothing is installed outside the project. The pre-commit hook scans staged changes; CI scans the full history. `.gitleaks.toml` uses gitleaks' built-in rules with no allowlist. Scans use `--redact`, so a found secret is never printed, and a missing gitleaks fails the hook instead of skipping it. On 2026-11-09 the full history scanned clean and a staged token-shaped test string was refused. The `pre-commit` framework is not used because it installs tools outside the project and does not work with `core.hooksPath`.

### D-032. CI: lint and tests only
`.github/workflows/ci.yml` (actions `checkout`, `setup-python`, `setup-node` at v7, read-only permissions) has two jobs. Python: ruff check, ruff format check, mypy, pytest, the personal-information check over all tracked files, and the gitleaks scan over the full history (`fetch-depth: 0`). Web: `npm ci`, lint, typecheck, Vitest, and a regenerate-and-diff of the API types. No model, knowledge base or network is needed, as `docs/TRD.md` section 14 requires. The pinned requirements install cleanly into a fresh virtual environment (`pip check` clean). First CI run: green on 2026-11-10 (UTC) for commit `f01f490`, both jobs, 58 seconds.

## Phase 1

### D-033. Business data source
`universe/` is Citedesk's `universe/` at commit `d64b8a0`, copied without changes; a test checks every file against Citedesk's `manifest.json` hashes. `universe/README.md` describes Citedesk's own generator, which is not part of Citebook; in Citebook, `tools/build_data.py` reads the universe.

### D-034. Generated seed files and knowledge base
`tools/build_data.py` reads `universe/facts.json` and `universe/citebook_extension.json` and writes `db/seed/` (services, technicians, customers, price book, settings) and `kb/docs/` (the universe documents plus `online-booking.md`). The outputs are committed so they can be read and reviewed; a test fails when they differ from what the generator writes. A second test checks that every fee, price, time limit and ZIP code in the seed files also appears in `kb/docs/`, so the assistant can never state a policy number the knowledge base does not contain. `price_book.version` is the first 8 hex digits of the SHA-256 of `facts.json`.

### D-035. Business time zone
`facts.json` has no time zone. The extension sets `America/Los_Angeles`. The four-week calendar (9 November to 6 December 2026) has no daylight-saving change; DST behavior is covered by unit tests in Phase 2.

### D-036. Saturday arrival windows
Weekday windows start at 8:00, 10:00, 12:00 and 14:00, so the last window ends at the published last arrival, 4:00 pm. Saturday allows arrivals from 8:00 to 1:00 pm, which does not divide into 2-hour windows; Saturday windows are 8:00–10:00 and 11:00–1:00 pm, so neither overlaps and both stay inside the published hours.

### D-037. Jobs over $300 are not bookable online
The business starts no work over $300 without an approved written estimate. Services priced over $300 (main sewer line cleaning, water heater replacements) are quoted in the chat but not bookable online (`bookable_online = 0`); staff arrange them. Quote-only services are never bookable online and name a bookable first step where one exists (`first_step_code`): camera inspection before sewer line repair, leak detection before slab leak or leak repair.

### D-038. Invented ZIP codes and technician skills
ZIP codes use the unassigned `000xx` range, so none matches a real place: one per neighbourhood, in its published zone. The two published technicians have different skills: Obadiah Finch does every service type, Lucia Marrow does not do inspections or sewer line repair, so availability differs by service.

### D-039. The business's own copy keeps its voice
`universe/docs/` and `kb/docs/` are customer-facing text in the invented business's voice: questions a customer asks in the first person, and policies that name the business's own roles. The universe copy must also stay byte-for-byte unchanged. The personal-information guard therefore skips its two wording rules in those folders only. Every other check, such as email addresses, phone numbers and local paths, still runs there, and a test covers both sides.

### D-040. Calendar seed
`tools/seed_calendar.py` builds one calendar for four weeks from 9 November 2026: one slot per technician per arrival window, stored in UTC, with no Sunday or closed-date slots. Seeded bookings mark their slots `booked` (both windows for a 2-window service). A seeded random share (`blocked_share`, 0.20) of slots is `blocked`, meaning the technician is busy elsewhere; random unavailability is never `booked`, because every `booked` slot must belong to a booking row. The random draw happens once per slot in a fixed order, so changing a booking never moves the blocked slots. Ids are UUIDv4-shaped values derived from a SHA-256 of the record's key, so they are the same on every run. A test pins the calendar's SHA-256 digest, so CI on Linux confirms the same calendar as Windows; `tzdata` is a runtime dependency (Apache-2.0) because Windows has no system time-zone database.

Seed states fix the clock: `base_week_1` starts Monday 9 November at 7:00 am and `base_week_2` Monday 16 November at 7:00 am, business time. Every seeded booking lies after both, and all are `confirmed`; holds are created by scenarios, because a hold's expiry depends on the scenario's clock. Seeded bookings get their quotes when Phase 2 loads the seed into the database.

## Phase 2

### D-041. First migration is the schema document
`db/migrations/001_init.sql` holds the SQL block of `docs/BACKEND_SCHEMA.md` section 3 exactly, followed by the indexes; a test fails if the two differ. `bookings(reference)` and `messages(conversation_id, seq)` already have indexes through their `UNIQUE` constraints. Migration files use three-digit numbers, as the schema document says. The runner comes from Citedesk: each migration and the row that records it commit together, and gaps, duplicates, bad names and a database newer than the code are refused.

### D-042. Seeded quotes have no conversation
Every booking needs a `quote_id`, and seeded bookings were never quoted in a chat, so `quotes.conversation_id` may be `NULL`, and only for quotes the seed creates. Tool code always sets it.

### D-043. Enums live in `citebook/models.py`
Each enum in schema section 2 is a `StrEnum` in `citebook/models.py` (except `service_code`, which is data). A test compares the code enums with the schema document and with every `CHECK (... IN (...))` constraint in the migrations, and fails if a `CHECK` constraint has no enum. `audit_events.actor` is listed as the enum `actor`. The `ablation` setting now uses the shared enum.

### D-044. How date phrases resolve
`citebook/timeutil.py` resolves the phrases a customer uses, in the business time zone, so the model never does date maths. A weekday name means the next such day after today; today is "today". "Next Friday" means the Friday of the next Monday-to-Sunday week, so on Thursday 12 November "Friday" is 13 November and "next Friday" is 20 November. "This Monday" after Monday has passed is unresolved, and the assistant asks which Monday. The card always shows the exact date, so the customer sees which day was chosen. Elapsed-time rules, such as 24 hours' notice, are computed in UTC, because Python subtracts two times in the same zone as wall-clock times and would be one hour wrong on a daylight-saving weekend; a test covers that weekend.

### D-045. Money helpers
Amounts are integer cents. Rates and dollar amounts are `Decimal`; a float, string or `bool` is refused with `TypeError`. Rounding is half up, away from zero (`$0.025` becomes 3 cents, not the 2 cents that banker's rounding gives). Display is `$1,450.00`, and a credit line is `-$89.00`.

### D-046. Emergency-rate visits get no call-out credit
The business says the call-out fee is credited to approved work, and that the emergency call-out fee replaces the standard one. It does not say the emergency fee is credited, so a quote for a visit outside the visit windows shows the $175 fee and no credit line. A quote never promises money back that the business has not published. Online slots are always inside the visit windows, so this rule matters only for quotes staff ask for.

### D-047. Execution and double booking
`citebook/actions.py` holds the state machines of schema section 4 as tables, and a test checks that the action table allows exactly the documented transitions. Confirmation and staff decisions run in one `BEGIN IMMEDIATE` transaction, which takes SQLite's write lock at the start, so two confirmations never interleave. Every slot change also checks the slot's status and `version`. Execution runs inside a savepoint: when a slot was taken meanwhile, everything rolls back to the savepoint and the action becomes `failed` with `failure_detail = slot_taken`, so nothing is half done. A test confirms the same slot from two threads on a file database at the same moment; exactly one hold is created and the other action fails.

### D-048. Lateness is decided when the customer confirms
Whether a reschedule or cancellation is late (less than `free_cancel_hours` before the visit) is computed at confirmation time, not at proposal time. Time only moves forward, so a change can only become late between the two moments, and the safe result follows: it goes to staff. A `held` booking can always be cancelled free. The payload hash binds the card to what was proposed; routing never relies on a value the model supplied.

### D-049. Database seed
`python -m tools.seed_db --fresh` migrates `CITEBOOK_DB_PATH` and loads settings, services, technicians, customers, the calendar and the seeded bookings. Each seeded booking gets a quote computed by `citebook/pricing.py`, with no conversation (D-042) and an id derived from its reference. The tool refuses to seed a database that already holds slots. Tests load the same seed into an in-memory database.

### D-050. Expiry details
`expire_due(now)` expires open proposals, unconfirmed holds and idle conversations in one idempotent transaction. Quotes have no status column: a quote is expired once `expires_at` has passed, and the gate refuses it then. An `active` or `awaiting_confirmation` conversation with no activity for `conversation_idle_minutes` (60) becomes `abandoned`, and its open proposal expires. A handed-off conversation never expires, because it waits for staff. Migration files never change once they are on `main`, so the new setting is described in prose in schema section 3, not in the SQL block that the first migration copies.

### D-051. Verification details
`citebook/verification.py` compares the last 4 digits of the booking's phone with `hmac.compare_digest`, and compares against a dummy value when the reference does not exist, so the timing is the same either way. Every failure returns the same words. Only `held` and `confirmed` bookings can be verified; a cancelled or completed booking fails like an unknown one. After `verification_max_attempts` (3) failures the result asks for a `VERIFICATION_FAILED` handoff, and later attempts are refused even with the right details, which stops guessing within one conversation. Audit events record the attempt count, never the reference or the digits.

### D-052. Screening reuses Citedesk's detectors
`citebook/screening.py` combines Citedesk's text normalisation, emergency net, sensitive-number masking, injection patterns and spam score in one module, and its tests are ported in `tests/test_screening_emergency.py` and `tests/test_screening_ported.py`. Changes for Citebook:

- Chat messages have no subject; `screen_message(text)` returns the masked text, the flags in `screen_flag` order, and pattern names as detail, never the matched text.
- The electrical pattern is removed: the business publishes no electrical safety advice, and the emergency reply may give only published advice.
- A new `uncontrolled_leak` pattern covers the business's "a leak that cannot be stopped". A clause that names a toilet is skipped, because a toilet that will not stop running is urgent but not an emergency.
- A small `ABUSIVE` detector (insults aimed at the assistant or the business, profanity, threats naming the person) is reported for staff and never used to refuse a customer. Spam is also report-only, and an emergency is never spam.
- The emergency reply uses only `facts.json` `safety_instructions` and the emergency line, which `tools/build_data.py` writes to `db/seed/safety.json`.

### D-053. Canary test for logs and audit events
`citebook/events.py` checks every value before an audit row or a log line is written: numbers, booleans, `None`, and short strings of letters, digits and `_ - . :`. It also refuses values made of those characters that still identify a customer: phone numbers (`555-0142`, `5550142`) and booking references (`CB-7KQ4MX`). Events carry row ids instead. `tests/test_canary.py` plants marker strings in a customer's name, phone, email and address, in booking notes, in a refund reason, in a staff rejection reason and in a screened message, runs every flow that writes events (screening, verification, propose, withdraw, confirm, staff decisions, handoff, close, expiry), and checks that no marker appears in `audit_events` or in the captured log. The current code passes only ids and codes to events; the test exists so that later code cannot change that unnoticed.

## Phase 3

### D-054. Outbox templates
`citebook/outbox.py` holds one fixed template per `outbox_kind`. Code fills it from the booking, its service, its first arrival window and its customer, so every draft names the reference, the service and the window ("Thursday 12 November, arrival 1:00–3:00 PM"). The hold draft says the booking is held, not confirmed, and gives `staff_confirm_typical_business_hours`. A declined hold includes the staff member's reason, as schema section 4.1 requires. `request_received` names the kind of request; a request for an exception without a booking names the customer only. Field values are inserted as text, so braces in a customer's name or a staff reason are never read as template fields. The module has no network code; drafts are only rows in `outbox`.

### D-055. Knowledge-base contract and relevance threshold
`citebook/kb.py` defines `KnowledgeBase` (`search(question, k)` and `max_distance`), `RagProjectKB` and `FakeKB`. `RagProjectKB` expects rag-project's retrieval-only endpoint to take `POST /api/search` with `{"question", "k"}` and return `{"passages": [{"source", "section", "text", "distance"}]}`, the passage fields rag-project already returns internally. It uses the standard library's `urllib`, so no new dependency is needed. An unreachable endpoint or an unusable reply is `KBUnavailable`, and `get_policy` then returns `found = false` with `unavailable = true`. `retrieve_policy` keeps up to 4 passages whose distance is at most the knowledge base's `max_distance`; with none left the result is `found = false`. `FakeKB` reads the `## ` sections of `kb/docs/`, and its distance is the share of the question's meaningful words that a section lacks; its threshold is 0.4, which keeps the policy questions in the tests and drops unrelated ones such as "Do you repair cars?". `RagProjectKB` starts at 0.6, to be tuned on dev scenarios once the endpoint exists. The integration test runs only with `CITEBOOK_KB_INTEGRATION=true`, so the default test run makes no network call; a stand-in HTTP server on `127.0.0.1` tests the contract. `tests/test_network_clients.py` allows network imports only in `citebook/kb.py` and `citebook/providers/`, and no module may import a messaging client.

### D-056. Tool details
Each tool is one module in `citebook/tools/` with a `SPEC`: name, kind, requirements, Pydantic argument model (unknown arguments refused), description for the model, handler, and the checks the gate runs for it. `citebook/tools/registry.py` lists them; a test compares the registry with schema section 2.1. Choices the documents leave open:

- `check_availability` searches at most 14 days, never offers a window that has already started, and treats "morning" as a start before 12:00 PM. Windows that start at the same time for two technicians are shown once, because the customer chooses a time, not a technician.
- A service that is not bookable online (jobs over $300) can be priced, with a note that a person arranges it, but `check_availability` and `propose_hold` refuse it. A `quote_only` service cannot be priced, and the hint names its first step from data.
- A `propose_*` card expires with its quote. A `request_*` card has no quote and expires after `quote_ttl_minutes`.
- While a card waits (`awaiting_confirmation`), every tool is allowed. A new `propose_*` or `request_*` card withdraws the open one first, so a customer who changes their mind ("don't move it, just cancel it") gets the new card at once. This removes no protection: the new card still needs Confirm, refunds and late changes still need staff, and the withdrawn card can no longer be confirmed. Refusing a request until the customer pressed Cancel would add a step that a small model can fail at, and three refusals in a row hand the conversation to staff.
- `collect_contact` is refused in a verified conversation, whose contact details come from the booking, and while a card waits, so the address on an open card cannot change under it. It never looks up existing customers. Phone numbers are stored as `555-0142` or `555-555-0142`.
- `find_booking` takes no arguments: it can only show the verified booking.
- `verify_customer` returns `escalate: VERIFICATION_FAILED` on the failure that reaches the limit; the turn loop creates the handoff with origin `loop`.
- `handoff_to_staff` accepts only reasons the model can know: `CUSTOMER_ASKED_FOR_PERSON`, `OUT_OF_SERVICE_AREA`, `NOT_IN_KNOWLEDGE_BASE`, `POLICY_EXCEPTION`, `SUSPICIOUS_INPUT`. Emergencies come from screening and failures from the loop.
- A card's summary is returned to the model in `untrusted_fields`, because it contains the address the customer typed.
- Limits per conversation, counting only calls the gate allowed: `check_availability`, `quote_price` and `get_policy` 10 each; `list_services`, `collect_contact` and each `propose_*` and `request_*` tool 5 each; `verify_customer` by `verification_max_attempts`.

### D-057. Gate details
`citebook/gate.py` runs the eight checks of TRD section 5 as separate functions, in order, and only reads the database. The ownership check belongs to check 3: a booking reference that is not the verified booking is `INVALID_ARGUMENTS`, with a hint to verify that booking first. In a conversation that is not verified, the ownership check passes the call on, so check 5 refuses it with the verification hint. Refusal hints name the fields and the problem but never repeat a value, which may be customer text. `citebook/tools/runner.py` records every proposed call in `tool_calls` with the gate's decision and runs an allowed tool in the same transaction, so an action and its tool call are written together. Audit events name an unknown tool `unknown`, because the name the model proposed is free text. The `baseline` and `no_gate_policy` ablations skip checks 2 and 4-7 and the ownership check; the tool's own SQL still finds only the verified booking of the verified customer, and a test covers both layers. No tool is `staff_only`, and a test checks that no tool module calls confirmation, staff-decision or take-over code.

## Phase 4

### D-058. Model providers
`citebook/providers/` holds the `ModelProvider` interface (`chat(messages, tools) -> ModelTurn` with text, tool calls, token counts and duration), `OllamaProvider` and `FakeProvider`. `OllamaProvider` calls `POST /api/chat` with `stream: false`, temperature 0, a fixed seed and `think` from `CITEBOOK_ENABLE_THINKING`, through the standard library's `urllib`. A timeout, a connection error, an HTTP error or a reply without a `message` is `ModelUnavailable`. Tool arguments are accepted as an object or as a JSON string; text that is not JSON is passed on unchanged, and the gate refuses it. Any `<think>` block in the text is removed. The contract test replays two replies recorded from the local `qwen3:8b` on Ollama 0.40.14 on 2026-11-10 (`tests/fixtures/`), one tool call and one text reply, through a stand-in server, and checks the request body against the recorded request. `FakeProvider` plays a script of turns, callables or errors and records every call, so tests can script malicious tool calls.
