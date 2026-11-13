# Security policy

Citebook is a demonstration project. It runs locally, uses invented data only, and is not offered as a hosted service. Version 1 has no staff login, so the API listens on `127.0.0.1` and must not be exposed to a network (see the README).

## Reporting a vulnerability

Report a suspected vulnerability privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue for it.

Please include what you found, how to reproduce it, and what it lets someone do. Reports are read and answered as time allows; there is no bug bounty.

## In scope

- A way to change a booking without the customer pressing the Confirm button.
- A way to execute a refund or send a message (neither path should exist).
- A way to see or change another customer's booking.
- A tool call that the policy gate should refuse but allows.
- A reply that shows invented prices, times or references, or another customer's data, and passes the reply checks.

Known limits of the reply checks and of the local model are listed in the README and in `eval/reports/citebook_v2_report.md`; a new example of a listed limit is still welcome.
