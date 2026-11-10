# Citebook

> **Work in progress.** This project is under construction and is not ready to use.

> **Demo data. The business, its staff, customers and calendar are invented.** Every name, email address, phone number, address, booking and price in this repository is synthetic. There is no real customer data here.

Citebook is a website chatbot for a small service business that acts, not only answers. A local model talks with the customer and chooses tool calls; plain code decides whether each call is allowed, computes every price and date, shows a confirmation card the customer must press, and sends holds, late changes, refunds and exceptions to a staff queue. Every reply is checked against the tool results before the customer sees it.

**Nothing changes a booking without the customer pressing Confirm, refunds are never executed, and no message is ever sent.** Notifications are drafts for staff.

## Status

Phase 0 (repository and tooling) of `docs/IMPLEMENTATION_PLAN.md`. Nothing that books, quotes or chats exists yet.

## Run it (Windows, PowerShell)

Instructions are added at the end of Phase 0.

## Documentation

Planning documents are in `docs/`. Start with `docs/PRD.md`; exact names and values are in `docs/BACKEND_SCHEMA.md` and behavior in `docs/TRD.md`. Decisions the plans do not cover are in `docs/DECISIONS.md`.

Part of a family of projects that share one invented plumbing business: [rag-project](https://github.com/Nbhaskell/rag-project), [citecheck](https://github.com/Nbhaskell/citecheck), [citedesk](https://github.com/Nbhaskell/citedesk), [citetally](https://github.com/Nbhaskell/citetally).

## License

MIT. See `LICENSE`.
