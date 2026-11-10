# Decisions

Decisions the planning documents do not cover, and changes made to them. Each entry says what was chosen and why. Newest last.

## Planning review (2026-11-09)

D-001 to D-025 come from a review of the planning documents against Citedesk's `universe/facts.json` before any code was written. The documents were changed to match.

### Company data (universe)

#### D-001. Price book follows `universe/facts.json` exactly
Quotes show the $89 call-out fee, the job price, and a negative line crediting the call-out fee when the work is approved, as the universe states. A fixed note says the call-out fee applies if the work is declined on the day. Reason: the earlier "call-out plus fixed price" example ($189) contradicted the universe.

#### D-002. No after-hours multiplier or weekend surcharge
The settings `after_hours_multiplier` and `weekend_surcharge_minor` are removed. The universe's $175 emergency call-out fee replaces the $89 fee outside scheduled-visit hours, on Sundays and closed dates; online slots lie only inside visit hours, so the fee never applies to an online quote. It stays in the knowledge base. Saturday is a normal visit day. `pricing_kind` gains `starting_price` and replaces `inspection_then_quote` with `quote_only`.

#### D-003. ZIP codes map to the universe's neighbourhoods and zones
Customers give a ZIP code. `zip_areas` (in `universe/citebook_extension.json`) maps each invented ZIP code to a neighbourhood and a zone: `core` (no fee) or `outer` ($40 travel line). A ZIP code not in the map is `OUT_OF_SERVICE_AREA`. Replaces `service_zip_codes`.

#### D-004. A slot is one 2-hour arrival window for one technician
This matches the universe's arrival windows. Each service has `skill` and `windows_needed` (1 or 2); availability searches only technicians with the skill. `quote_only` services are not bookable online (enforced by a `CHECK`); the model offers the bookable first step, such as a camera inspection. Replaces `services.duration_minutes`.

#### D-005. Late reschedules go to staff, not `POLICY_BLOCKED`
The universe allows a reschedule with less than 24 hours' notice for a $45 fee. A late reschedule is allowed, confirmed by the customer, and sent to staff with the new reason `LATE_RESCHEDULE`. Exceeding `max_reschedules` stays `POLICY_BLOCKED`. Replaces `reschedule_min_hours`.

#### D-006. Universe copied unchanged; additions in an extension file
`universe/` is copied from Citedesk without edits; the source commit is recorded here at copy time (Citedesk `main` was at `d64b8a0` on 2026-11-09). Additions go in `universe/citebook_extension.json`; `facts.json` wins where they disagree, and a test checks they agree. Invented customer phones use 555-0100 to 555-0169 because the business uses 555-0171 and 555-0172.

### Contradictions between documents

#### D-007. Late-change wording
"Late" means the visit starts less than `free_cancel_hours` from now. The earlier wording ("inside the free-cancellation window") read as the opposite.

#### D-008. Every propose and request action needs Confirm
Only `handoff` skips it. Schema 2.1 is corrected to match 2.3 and the state machine.

#### D-009. Sealed split
At least a third of sealed scenarios come from families with no dev scenarios; the rest are new scenarios in dev families. Reports show "new family" and "known family" separately. Chosen over "all sealed families unseen" because it measures both kinds of generalization.

#### D-010. `awaiting_confirmation → abandoned` added
A customer can leave while a card is open.

#### D-011. Citebook is the sixth project in the suite
The PRD said "fourth" while listing five others.

### Gaps

#### D-012. Actions record their origin
New column `actions.origin` (`tool`, `screening`, `loop`); `tool_call_id` is set only for `tool`. Reason: emergency and loop escalations have no tool call, and the old `NOT NULL` would have rejected them.

#### D-013. Handoff lifecycle
A handoff is created directly in `pending_staff` and moves to `executed` when staff take over or close. Handing off withdraws any open proposal.

#### D-014. `hold_release` removed
No tool created it. A customer cancels a held booking with `request_cancellation`, which is always free for a hold.

#### D-015. Expired and declined holds
Added `pending_staff → expired` for holds and the outbox kinds `hold_expired` and `hold_declined`.

#### D-016. One expiry function
`expire_due(now)` handles quotes, proposals, holds and abandoned conversations. It runs at the start of each request and every 60 seconds in a background task. Tests pass a fixed time.

#### D-017. Hold time counts office hours only
`hold_ttl_business_hours` replaces `hold_ttl_minutes`. Reason: a hold placed at night would otherwise expire before staff can see it, which defeats out-of-hours booking.

#### D-018. Reschedule needs a new quote
`propose_reschedule(booking_reference, slot_id, quote_id)`. The card shows old and new totals when they differ; the booking's `quote_id` is replaced.

#### D-019. Reply-check allowed values widened
`PRICE_FROM_TOOL` accepts amounts from quotes, policy passages and settings/price book. `TIME_FROM_TOOL` accepts dates code wrote in the state summary. Reason: otherwise correct replies quoting the $45 late fee or the resolved date would fail.

#### D-020. No `reschedule_needs_staff` setting
D-005 routes late reschedules by `free_cancel_hours`.

#### D-021. Verification scope
Verification opens only the verified booking, in the gate and in SQL. A verified conversation counts as having contact details, taken from that booking's customer.

#### D-022. Ablation switch
`CITEBOOK_ABLATION` replaces `CITEBOOK_BASELINE_MODE`, with one value per removable layer (TRD section 9). Button confirmation and staff approval have no off switch, because switching them off needs a code path that executes on the model's word. SQL ownership also stays on, so the baseline understates `other_customer_data` attack success; the report says so.

#### D-023. Knowledge base through a retrieval-only rag-project endpoint
`RagProjectKB` will call a new `POST /api/search` that returns passages without a model call. `/api/ask` is rejected because it runs a full model answer (about 8 s, thinking on) on the same Ollama instance for each policy question. The endpoint is a separate rag-project change with its own approval; until then `FakeKB` is used. rag-project serves one documents folder at a time, so Citebook and Citedesk cannot use it at the same moment.

#### D-024. Direction of the 32/45 comparison
rag-project's figure counts attempts resisted (32 of 45, about 29% attack success). Citebook reports attack success; the report states both directions.

### Repository rules

#### D-025. `CLAUDE.md` stays untracked; no personal descriptions in it
`CLAUDE.md` and `CLAUDE.local.md` are git-ignored, as in Citedesk, Citetally and rag-project. (An earlier draft of this entry said the sister projects track `CLAUDE.md`; a check of their `.gitignore` files on 2026-11-09 showed they do not.) The environment line is written as an instruction ("Give Windows PowerShell commands first."). The PRD's latency target refers to "the reference machine", whose specifications go in the report.

## Phase 0

### D-026. Directories and sub-packages are created, empty module files are not
`docs/TRD.md` section 3 lists every module. Phase 0 creates the directories (`db/migrations`, `db/seed`, `eval/scenarios`, `eval/reports`, `tools`, `tests`, `web`) and the sub-packages (`citebook/tools`, `citebook/providers`, `citebook/prompts`). Module files such as `gate.py` and `money.py` are created in the phase that implements them, so there are no empty stubs to mislead a reader. `universe/` is created in Phase 1, when it is copied. Two folders named `tools` exist on purpose: `citebook/tools/` holds the model's tools, the top-level `tools/` holds developer scripts.

### D-027. Line endings: LF
`.gitattributes` sets `* text=auto eol=lf` so Windows checkouts and the Linux CI runner agree and ruff's formatter does not report line-ending differences.

### D-028. Python dependencies are pinned in two requirements files
`requirements.txt` holds runtime dependencies and `requirements-dev.txt` adds test and lint tools (`-r requirements.txt` plus pytest, ruff, mypy, httpx2), the same layout as Citetally. Both list every package, including transitive ones, at exact versions (a `pip freeze`), so installs are reproducible. No extra lock tool. Phase 0 installs only what Phase 0 uses. FastAPI's test client uses `httpx2` (BSD-3-Clause), which Starlette now recommends over `httpx` and which avoids `certifi` (MPL-2.0).

Type checking is `mypy --strict` with the pydantic plugin. Lint and format are `ruff` (rules `E, F, I, B, UP, SIM, RUF`, line length 100), which skips `docs/` and `web/` because recent ruff versions also format Markdown code blocks and the planning documents should not be rewritten by a formatter.

### D-029. License check of the Python dependencies
Every installed package was checked with a metadata-reading script on 2026-11-09: 32 packages, all MIT, BSD, Apache-2.0 or PSF, with one exception: `pathspec` 1.1.1 (MPL-2.0, weak file-level copyleft), pulled in by `mypy`. It is development-only, unmodified and not redistributed, so it does not affect the MIT license of this repository. Citetally accepted the same package on the same grounds. **Provisional** until confirmed for this repository. No AGPL or GPL.

### D-030. Settings use the names in TRD section 12, in one class, with `.env` optional
`citebook/config.py` has a single `Settings` class (pydantic-settings). Each field is bound to its documented variable name (`CITEBOOK_*`, plus `OLLAMA_URL`), empty values fall back to defaults so `.env.example` can ship with blank entries, and unknown variables are ignored. Phase 0 includes only the settings the status route and the safety switches need; the turn-loop limits arrive in Phase 4. Two safety switches are enforced at startup and tested: `CITEBOOK_MODEL_PROVIDER=hosted` needs `CITEBOOK_ALLOW_HOSTED=true` (rule 11), and any `CITEBOOK_ABLATION` other than `none` needs `CITEBOOK_EVAL=true` (rule 9). Service URLs must be `http://` or `https://`. Until `citebook/models.py` exists (Phase 2), the `ablation` values are a `Literal` in `config.py`; Phase 2 moves them into the shared enums.

The server's bind address is a constant, not a setting: `python -m citebook` always binds to `127.0.0.1`, because v1 has no authentication. The app is built by `create_app(settings)`, not a module-level global, so tests never read a local `.env`.

### D-031. `/api/status` reports only what exists
The status response names the service, version, provider, model, synthetic-data flag and the ablation in force. `model_reachable`, `kb_reachable` and `database` are `null` ("not checked yet") until the phases that build those parts, so the page never shows a green check for something unchecked.

### D-032. Web setup: one Vite project, two entry points, generated API types
`web/` is a hand-written Vite + React + TypeScript project (no template generator) with two pages: `/widget/` (customer widget) and `/console/` (staff console), plus a plain index page linking both in development. Shared code lives in `web/src/shared/`. The dev server runs on `127.0.0.1:5176` and proxies `/api` to `127.0.0.1:8300`, so the API needs no CORS settings. Versions are pinned exactly and `package-lock.json` is committed. TypeScript is held at 5.9.3: 7.x is outside the peer ranges of `typescript-eslint` (`<6.1`) and `openapi-typescript` (`^5.x`).

API types are generated: `python -m tools.export_openapi` writes `web/openapi.json`, `npm run gen:api` writes `web/src/api/schema.d.ts`, both are committed, and a pytest test fails when `openapi.json` no longer matches the app. Both pages show the demo-data label from `docs/UI_UX_DESIGN_BRIEF.md` and a banner when an evaluation ablation is in force. All server text is rendered as text.

### D-033. License check of the npm packages
237 packages checked on 2026-11-09. All are MIT, Apache-2.0, ISC, BSD, BlueOak or Python-2.0, with three exceptions, all build-time only and not shipped as source: `lightningcss` and its Windows binary (MPL-2.0, a Vite dependency) and `caniuse-lite` (CC-BY-4.0 browser data). Citetally accepted the same packages on the same grounds. **Provisional** until confirmed for this repository.

### D-034. Environment check reports, never installs
`tools/check_env.py` uses only the standard library, so it runs before the virtual environment exists. It reports Python 3.13, Node.js, Ollama, whether the configured model (`CITEBOOK_MODEL`, default `qwen3:8b`) is pulled and lists tool calling in `POST /api/show` capabilities, and whether rag-project answers `GET /api/status` at `CITEBOOK_KB_URL`. Failures print a Windows install or start hint. It never installs anything and exits 0 unless `--strict`, so it cannot fail the test suite. All machine access goes through an injected `Probes` object, so its tests need no network or binaries. rag-project's retrieval-only endpoint (D-023) is not checked yet; the check is added when the endpoint exists.

### D-035. Personal-information guard on every commit and in CI
`tools/check_personal_info.py` runs from `.githooks/pre-commit` (staged files) and `.githooks/commit-msg` (the message), and with `--all` in CI. It flags local user-profile paths, email addresses and phone numbers that are not invented ones, notes that describe the author, tracked assistant-instruction files, a commit email that is not a GitHub noreply address, and, in Markdown and commit messages, wording that names a person as the actor or uses first person, plus assistant attribution lines. Findings show file, line and label, never the matched text. Enable once per clone with `git config core.hooksPath .githooks`. Both hooks were shown to refuse a commit on 2026-11-09 (a staged file with an email and a local path; a message with an attribution line; a first-person message).

The first `--all` run flagged six lines of in-product text in the planning documents: the business-owner persona and quoted customer and assistant lines. They were reworded ("the business owner", "As an owner, I ...", "please give the reference") so the guard stays strict. It does not catch everything: names of real people, or personal details written in plain words, are left to review.

### D-036. Secret scan with a pinned, checksum-verified gitleaks
`tools/secret_scan.py` downloads gitleaks 8.30.1 (the version Citetally uses) into the git-ignored `.tools/` folder and checks it against SHA-256 values pinned in the script, so nothing is installed outside the project. The pre-commit hook scans staged changes; CI scans the full history. `.gitleaks.toml` uses gitleaks' built-in rules with no allowlist. Scans use `--redact`, so a found secret is never printed, and a missing gitleaks fails the hook instead of skipping it. On 2026-11-09 the full history scanned clean and a staged token-shaped test string was refused. The `pre-commit` framework is not used because it installs tools outside the project and does not work with `core.hooksPath`.

### D-037. CI: lint and tests only
`.github/workflows/ci.yml` (actions `checkout`, `setup-python`, `setup-node` at v7, read-only permissions) has two jobs. Python: ruff check, ruff format check, mypy, pytest, the personal-information check over all tracked files, and the gitleaks scan over the full history (`fetch-depth: 0`). Web: `npm ci`, lint, typecheck, Vitest, and a regenerate-and-diff of the API types. No model, knowledge base or network is needed, as `docs/TRD.md` section 14 requires. The same commands passed locally on 2026-11-09, and the pinned requirements installed cleanly into a fresh virtual environment (`pip check` clean, 100 tests passed).

First CI run: green on 2026-11-10 (UTC) for commit `f01f490`, both jobs, 58 seconds.
