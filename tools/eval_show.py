"""Print one saved scenario run as a transcript, for error analysis.

python -m tools.eval_show eval/runs/dev-1 S-014 [--repeat 1]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def show(record: dict[str, Any]) -> str:
    lines = [f"== {record['id']} repeat {record['repeat']}"]
    for step in record["steps"]:
        body = step.get("body") or {}
        if step["kind"] != "message":
            outcome = step.get("result") or body.get("outcome") or body.get("error", {}).get("code")
            lines.append(f"CUSTOMER presses {step['kind']} ({step.get('card_type')}) -> {outcome}")
            reply = body.get("reply") if isinstance(body, dict) else None
            if reply:
                lines.append(f"  ASSISTANT ({reply['kind']}): {reply['text']}")
            continue
        lines.append(f"CUSTOMER: {step['input']}")
        for reply in body.get("replies", []):
            lines.append(f"  ASSISTANT ({reply['kind']}): {reply['text']}")
        if body.get("quote"):
            lines.append(f"  QUOTE total {body['quote']['total']}")
        if body.get("slots"):
            lines.append(f"  CHIPS {[s['when'] for s in body['slots']]}")
        if body.get("card"):
            card = body["card"]
            lines.append(f"  CARD {card['title']}: {' | '.join(card['rows'])}")
        if "error" in body:
            lines.append(f"  HTTP {step.get('http')} {body['error']}")
    after = record["after"]
    tools = [f"{t['tool']}:{t['gate']}:{t['ok']}" for t in after["tools"]]
    lines.append("tools: " + ", ".join(tools))
    lines.append(
        "actions: "
        + ", ".join(
            f"{a['action_type']}:{a['status']}:{a['escalation_reason']}" for a in after["actions"]
        )
    )
    failed = [m["failed"] for m in after["shown"] if m["failed"]]
    if failed:
        lines.append(f"shown replies with failed checks: {failed}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("ids", help="comma-separated scenario ids")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args(argv)
    wanted = set(args.ids.split(","))
    for line in (args.run / "results.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["id"] in wanted and record["repeat"] == args.repeat:
            print(show(record) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
