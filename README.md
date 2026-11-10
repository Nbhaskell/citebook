# Citebook

> **Work in progress.** This project is under construction and is not ready to use.

> **Demo data. The business, its staff, customers and calendar are invented.** Every name, email address, phone number, address, booking and price in this repository is synthetic. There is no real customer data here.

Citebook is a website chatbot for a small service business that acts, not only answers. A local model talks with the customer and chooses tool calls; plain code decides whether each call is allowed, computes every price and date, shows a confirmation card the customer must press, and sends holds, late changes, refunds and exceptions to a staff queue. Every reply is checked against the tool results before the customer sees it.

**Nothing changes a booking without the customer pressing Confirm, refunds are never executed, and no message is ever sent.** Notifications are drafts for staff.

## Status

Phases 0 to 2 of `docs/IMPLEMENTATION_PLAN.md` are complete:

- Phase 0: settings, a `/api/status` route, a placeholder widget and staff console that show the status, an environment check, commit guards and CI.
- Phase 1: the invented business copied from Citedesk's `universe/`, Citebook's extension (services, technicians, ZIP codes, customers, bookings), generated seed files and knowledge-base documents, and a deterministic four-week calendar seed (`python -m tools.seed_calendar --state base_week_1`).
- Phase 2: the database (migrations kept equal to the schema document), enums kept in step with every `CHECK` constraint, money and business-time helpers, quote pricing, the action state machine with transactional execution and a double-booking test, expiry of cards, holds and idle conversations, customer verification, screening of customer messages, and a canary test that keeps message content out of logs and audit events.

There is no chat yet: the tools, the gate and the model come in Phases 3 and 4.

## Run it (Windows, PowerShell)

Needs Python 3.13, Node.js 20.19 or newer and, from Phase 4, Ollama with `qwen3:8b`. From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm --prefix web ci
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m citebook
```

In a second terminal, start the web app and open `http://127.0.0.1:5176/widget/` or `http://127.0.0.1:5176/console/`:

```powershell
npm --prefix web run dev
```

Check the machine (reports only; installs nothing):

```powershell
.\.venv\Scripts\python.exe tools\check_env.py
```

Run the checks:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web test
```

Turn on the commit guards once per clone:

```powershell
git config core.hooksPath .githooks
.\.venv\Scripts\python.exe tools\secret_scan.py --fetch
```

## Documentation

Planning documents are in `docs/`. Start with `docs/PRD.md`; exact names and values are in `docs/BACKEND_SCHEMA.md` and behavior in `docs/TRD.md`. Decisions the plans do not cover are in `docs/DECISIONS.md`.

Part of a family of projects that share one invented plumbing business: [rag-project](https://github.com/Nbhaskell/rag-project), [citecheck](https://github.com/Nbhaskell/citecheck), [citedesk](https://github.com/Nbhaskell/citedesk), [citetally](https://github.com/Nbhaskell/citetally).

## License

MIT. See `LICENSE`.
