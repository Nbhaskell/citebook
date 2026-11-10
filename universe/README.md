# universe: the invented business

> **SYNTHETIC TEST DATA. All customers, staff, prices and messages are invented.** Kestrel Hollow Plumbing does not exist. Phone numbers are in the 555-01xx range and emails are at example.com. A real plumbing company's services menu was used only as a reference for which standard services exist; no names, wording or prices were copied.

This folder holds the fictional plumbing company that Citedesk is built and measured against.

| Path | What it is |
|---|---|
| `facts.json` | The single source of truth: prices, hours, policies, staff, service area. |
| `docs/` | The knowledge-base documents, generated from `facts.json`. |
| `manifest.json` | Hashes of the fact sheet and each document, and how they were generated. |

The labelled inquiries are in `eval/golden/` (see its README).

## Rebuild and check

Fix the generator, never the generated files.

```powershell
.\.venv\Scripts\python.exe -m tools.generate_kb_docs      # writes docs/ and manifest.json
.\.venv\Scripts\python.exe -m tools.check_consistency     # fails if any document disagrees with facts.json
```

The test suite also checks that the committed documents are exactly what the generator produces.

## What this data can and cannot show

- It shows that the pipeline works end to end on a consistent business: routing, checks, escalation and review behave as designed.
- It does **not** show how the system behaves on a real business's inbox. Real documents are messier, contradict themselves, and go out of date.
- The documents are cleaner and more uniform than anything a real business writes. They come from one template and one fact sheet, so retrieval is easier here than it would be in practice.
- The text was produced by the same process that produced the fact sheet, so a model may find it easier than a stranger's writing would be.
- A real pilot with a willing business, with consent and personal details removed, would tell more than any amount of synthetic data.

## Known gaps

- No PDF document. `rag-project` can read PDFs, but no scenario needs one yet.
- Live availability is deliberately absent, so availability questions cannot be answered from the documents.
- Retrieval is imperfect. In a small check, a question about plumbing at a restaurant was still answered "yes" although commercial buildings are not served. See `docs/knowledge-base-contract.md`.
