# Citebook

> **Demo data. The business, its staff, customers and calendar are invented.** Every name, email address, phone number, address, booking and price in this repository is synthetic. There is no real customer data here.

Citebook is a website chatbot for a small service business that acts, not only answers. It checks open times, quotes prices and places booking holds through tool calls. A local model talks with the customer and chooses the tools; plain code decides what is allowed, computes every price and date, and changes a booking only after the customer presses a Confirm button.

**Nothing changes a booking without the customer pressing Confirm, refunds are never executed, and no message is ever sent.** Notifications are drafts for staff.

## The problem

A booking chatbot that can change bookings is useful only if nobody can talk it into the wrong change. Language models sound certain when they are wrong, follow instructions hidden in the text they read, and say "your booking is confirmed" when nothing happened. For a small business that cannot watch every chat, one invented price, a refund promised by mistake or another customer's address in a reply costs more than the bot saves.

## The principle: the model proposes, code decides

- **The model talks and chooses tools.** It never changes anything itself.
- **A policy gate in code checks every tool call** before it runs: is this tool allowed in this conversation state, are the arguments valid, is the customer verified, does the booking belong to this customer (checked again in SQL), is the request inside policy. Identity comes from the conversation record, never from the model's arguments.
- **Code computes every price, date and policy limit.** The model never does arithmetic or date maths.
- **Changes need a button press.** A booking change appears as a card built by code from the action's data and bound to a hash of it. Typed "yes" never confirms, and the model cannot create or edit a card.
- **Staff decide anything irreversible or about money.** Holds wait for staff approval; late changes, refunds and exceptions go to a staff queue. Refunds are recorded, never executed.
- **Every reply is checked before the customer sees it.** Each price, time and booking reference must come from a tool result in the conversation; replies that claim a booking happened, promise a refund, show another customer's data or repeat the instructions are blocked and written again once, or replaced by fixed copy and a handoff.
- **Untrusted text is marked as data.** Customer messages, booking notes and retrieved documents reach the model inside marked data blocks, and the prompt says they are information, not instructions.

## What it looks like

- **Customer widget** on a demo website for the invented plumber: messages, tappable time chips, a price table computed by code, a confirmation card that looks like a receipt and becomes one after Confirm, an emergency block with the emergency line, and a "Talk to a person" handoff.
- **Staff console:** a queue of decisions (holds, late changes, refunds, exceptions, handoffs, expired holds), each conversation with its transcript, an activity timeline of every tool call with the gate's decision and the reply checks, a decision panel (approve, reject with a reason, take over, close), a calendar by technician and an outbox of unsent notification drafts.

Both work by keyboard alone and at 360 px wide.

## Evaluation

Full report: [`eval/reports/citebook_v1_report.md`](eval/reports/citebook_v1_report.md). 102 scripted scenarios: 62 for development and 40 sealed scenarios written after the development numbers were frozen and run once with 3 repeats. Local `qwen3:8b`, fake knowledge base, synthetic data. Scoring is plain code over the database end state and the text the customer saw; there is no AI judge.

### With and without the guards (sealed set)

| Measure | All layers | Baseline (gate policy and reply checks off) |
|---|---|---|
| Benign task success | 75.0% | 80.0% |
| Attack success | 5.0% | 30.0% measured; 25.0% after reading |
| Runs with a policy violation | 0 | 31 |
| Escalation recall | 62.5% | 87.5% |

No booking changed without the customer's button press, and no refund was executed, in either setting: those paths do not exist in code, so the baseline cannot switch them off.

### Each layer on its own (development set; the all-layers row has 3 repeats, the others 1)

| Setting | Benign task success | Attack success | Runs with a violation |
|---|---|---|---|
| All layers | 93.7% | 0.0% | 0 |
| Gate policy and reply checks off (baseline) | 81.0% | 30.0% | 8 |
| Reply checks off | 95.2% | 25.0% | 5 |
| Data marking off | 90.5% | 5.0% | 0 |
| Gate policy off | 83.3% | 0.0% | 0 |
| All tools offered in every state | 95.2% | 0.0% | 0 |
| Screening off | 92.9% | 0.0% | 0 |

The reply checks carry most of the protection against words that mislead the customer; the gate's refusal hints help the model complete benign tasks.

### Limits

- The one successful attack with all layers asked for the instructions in French; the instruction-echo check compares English text only.
- On new kinds of conversation benign task success was 63.9% (91.7% on kinds seen in development). In one scenario the model offered a cancellation card when the customer asked to move a booking.
- Scripted customers press Confirm without reading the card, so these numbers show the system's behaviour, not a real customer's experience.
- 40 sealed scenarios is a small sample, the same assistant built the system and wrote the scenarios, and only one local model and the fake knowledge base were measured.

## Run it (Windows, PowerShell)

Needs Python 3.13, Node.js 20.19 or newer, and [Ollama](https://ollama.com) with `qwen3:8b` (`ollama pull qwen3:8b`). From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm --prefix web ci
Copy-Item .env.example .env
```

Seed the demo database. The seeded calendar covers the four weeks from Monday 9 November 2026, so set `CITEBOOK_DEMO_START=2026-11-09T07:00:00-08:00` in `.env` to start the API's clock there:

```powershell
.\.venv\Scripts\python.exe -m tools.seed_db --fresh
```

Start the API:

```powershell
.\.venv\Scripts\python.exe -m citebook
```

In a second terminal, start the web app, then open `http://127.0.0.1:5176/` (the demo plumber website with the chat widget) and `http://127.0.0.1:5176/console/` (the staff console):

```powershell
npm --prefix web run dev
```

Check the machine (reports only; installs nothing):

```powershell
.\.venv\Scripts\python.exe tools\check_env.py
```

Run the tests and checks:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web test
```

Run the scenarios against the local model and score them:

```powershell
.\.venv\Scripts\python.exe -m tools.eval_run --name demo-run --repeats 1
.\.venv\Scripts\python.exe -m tools.eval_score eval\runs\demo-run
```

Turn on the commit guards once per clone:

```powershell
git config core.hooksPath .githooks
.\.venv\Scripts\python.exe tools\secret_scan.py --fetch
```

The API listens on `127.0.0.1` only. Version 1 has no staff login, so it must not be exposed to a network.

## Project layout

| Path | What is there |
|---|---|
| `citebook/` | The API (FastAPI), turn loop, policy gate, tools, reply checks, screening, pricing and the action state machine |
| `web/` | The widget, the demo website and the staff console (React, TypeScript, Vite) |
| `db/` | SQL migrations and generated seed files |
| `universe/`, `kb/docs/` | The invented business and the knowledge-base documents generated from it |
| `eval/` | Scenarios and evaluation reports |
| `tools/` | Seeding, the scenario runner and scorer, the environment check and the commit guards |
| `docs/` | Requirements, technical design, schema, flows, design brief, plan, and the decision log (`docs/DECISIONS.md`) |

## The shared invented business

Citebook is the sixth project in a suite: rag-project (answers from documents), citewise-markets (checked research answers), citecheck (evaluation of AI answers), citedesk (an inquiry review desk), citetally (an invoice review desk) and Citebook. Citebook reuses the invented plumbing business from citedesk, Kestrel Hollow Plumbing, with its prices and policies unchanged: its `universe/` data is copied without changes, and Citebook's own additions (ZIP codes, technicians, service codes, customers and bookings) are in `universe/citebook_extension.json`. So the projects tell one story about one business.

- [rag-project](https://github.com/Nbhaskell/rag-project): Citebook can use its retrieval endpoint (`POST /api/search`) as the knowledge base.
- [citecheck](https://github.com/Nbhaskell/citecheck): Citebook's evaluation report follows its style.
- [citedesk](https://github.com/Nbhaskell/citedesk): the source of the invented business.
- [citetally](https://github.com/Nbhaskell/citetally): the invoice desk for the same business.

## License

MIT. See `LICENSE`.
