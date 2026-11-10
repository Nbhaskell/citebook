"""Scenario runner (TRD section 15): drives the real HTTP API and saves raw results, no scores.

    python -m tools.eval_run --name dev-1 [--split dev] [--repeats 3] [--only S-001,S-002]
        [--ablation none] [--think] [--kb fake]

Each scenario runs on a fresh seeded database at its seed state's time, through the FastAPI app
(the same routes the widget uses) with the real model provider. "{confirm}" and "{cancel}" press
the open card's buttons with its payload hash; every other line is a customer message. Results go
to eval/runs/<name>/results.jsonl, one line per scenario and repeat, written as they finish, so
an interrupted run resumes where it stopped. The scorer (tools.eval_score) reads that file.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from citebook.api import create_app
from citebook.config import Settings
from citebook.db import connect, migrate
from citebook.kb import FakeKB, KnowledgeBase, Passage, RagProjectKB
from citebook.models import Ablation
from citebook.prompts import PROMPT_VERSION
from citebook.providers.base import ModelProvider
from citebook.providers.ollama import OllamaProvider
from tools.eval_common import CANCEL, CONFIRM, SCENARIO_FILE, file_hash, load_scenarios
from tools.seed_calendar import state_now
from tools.seed_db import load_seed

RUNS = Path("eval/runs")
STEP = timedelta(minutes=1)


class StepClock:
    """The app's clock: the seed state's time, moved on one minute per scripted step."""

    def __init__(self, start: datetime) -> None:
        self.now = start.astimezone(UTC)

    def __call__(self) -> datetime:
        return self.now

    def step(self) -> None:
        self.now += STEP


class InjectedKB:
    """A knowledge base with planted passages returned first (attack category indirect_via_kb)."""

    def __init__(self, base: KnowledgeBase, planted: list[dict[str, str]]) -> None:
        self.base = base
        self.max_distance = base.max_distance
        self.planted = [Passage(p["source"], p["section"], p["text"], 0.0) for p in planted]

    def reachable(self) -> bool:
        check = getattr(self.base, "reachable", None)
        return bool(check()) if callable(check) else True

    def search(self, question: str, k: int) -> list[Passage]:
        return (self.planted + self.base.search(question, k))[:k]


def git_state() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False
        ).stdout.strip()

    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def fresh_db(folder: Path) -> Path:
    path = folder / "eval.db"
    conn = connect(path)
    try:
        migrate(conn)
        load_seed(conn)
    finally:
        conn.close()
    return path


def snapshot(path: Path, conversation_id: str | None) -> dict[str, Any]:
    """The end state the scorer needs, read straight from the database."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        bookings = [
            dict(r)
            for r in conn.execute(
                "SELECT b.reference, b.status, b.service_code, b.reschedule_count,"
                " c.name AS customer,"
                " c.phone, s.starts_at, s.ends_at FROM bookings b JOIN customers c"
                " ON c.id = b.customer_id JOIN slots s ON s.id = b.slot_id ORDER BY b.reference"
            )
        ]
        customers = [
            dict(r) for r in conn.execute("SELECT name, phone, email, address FROM customers")
        ]
        if conversation_id is None:
            return {"bookings": bookings, "customers": customers}
        actions = [
            dict(r)
            for r in conn.execute(
                "SELECT a.action_type, a.status, a.escalation_reason, a.origin,"
                " a.customer_confirmed_at, b.reference FROM actions a LEFT JOIN bookings b"
                " ON b.id = a.booking_id WHERE a.conversation_id = ? ORDER BY a.created_at",
                (conversation_id,),
            )
        ]
        tools = [
            {"tool": r["tool"], "gate": r["gate_decision"], "ok": _ok(r["result"])}
            for r in conn.execute(
                "SELECT tool, gate_decision, result FROM tool_calls WHERE conversation_id = ?"
                " ORDER BY rowid",
                (conversation_id,),
            )
        ]
        shown = []
        for row in conn.execute(
            "SELECT id, content FROM messages WHERE conversation_id = ? AND role = 'assistant'"
            " ORDER BY seq",
            (conversation_id,),
        ):
            checks = conn.execute(
                "SELECT attempt, check_code, passed FROM reply_checks WHERE message_id = ?",
                (row["id"],),
            ).fetchall()
            last = max((c["attempt"] for c in checks), default=0)
            failed = sorted(
                c["check_code"] for c in checks if c["attempt"] == last and not c["passed"]
            )
            regenerated = last > 1
            shown.append({"text": row["content"], "failed": failed, "regenerated": regenerated})
        conv = conn.execute(
            "SELECT status, verification_state FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        outbox = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        return {
            "bookings": bookings,
            "customers": customers,
            "actions": actions,
            "tools": tools,
            "shown": shown,
            "conversation": dict(conv),
            "outbox": outbox,
        }
    finally:
        conn.close()


def _ok(result: str | None) -> bool | None:
    return None if result is None else bool(json.loads(result).get("ok"))


def run_scenario(
    scenario: dict[str, Any],
    repeat: int,
    provider: ModelProvider,
    kb: KnowledgeBase,
    ablation: str,
    think: bool,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="citebook-eval-") as folder:
        db_path = fresh_db(Path(folder))
        before = snapshot(db_path, None)
        settings = Settings(
            _env_file=None,
            CITEBOOK_DB_PATH=str(db_path),
            CITEBOOK_EVAL="true",
            CITEBOOK_ABLATION=ablation,
            CITEBOOK_ENABLE_THINKING=str(think).lower(),
        )
        clock = StepClock(state_now(scenario["seed_state"]))
        planted = scenario.get("kb_inject") or []
        app = create_app(
            settings, provider=provider, kb=InjectedKB(kb, planted) if planted else kb, clock=clock
        )
        client = TestClient(app)
        created = client.post("/api/conversations", json={"channel": "simulator"}).json()
        conversation_id, token = created["conversation_id"], created["session_token"]
        headers = {"X-Session-Token": token}
        steps: list[dict[str, Any]] = []
        card: dict[str, Any] | None = None
        for index, line in enumerate(scenario["script"]):
            clock.step()
            step: dict[str, Any] = {"index": index, "input": line}
            started = time.perf_counter()
            if line in (CONFIRM, CANCEL):
                step["kind"] = "confirm" if line == CONFIRM else "cancel"
                if card is None:
                    step["result"] = "no_card"
                else:
                    if line == CONFIRM:
                        response = client.post(
                            f"/api/actions/{card['action_id']}/confirm",
                            json={"payload_hash": card["payload_hash"]},
                            headers=headers,
                        )
                    else:
                        response = client.post(
                            f"/api/actions/{card['action_id']}/cancel", headers=headers
                        )
                    step["http"] = response.status_code
                    step["body"] = response.json()
                    step["card_type"] = card["action_type"]
                    card = None
            else:
                step["kind"] = "message"
                response = client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={"text": line},
                    headers=headers,
                )
                step["http"] = response.status_code
                body = response.json()
                step["body"] = body
                if response.status_code == 200 and body.get("card"):
                    card = body["card"]
            step["seconds"] = round(time.perf_counter() - started, 3)
            steps.append(step)
            if step.get("http") == 409 and step["body"].get("error", {}).get("code") == (
                "CONVERSATION_ENDED"
            ):
                break
        after = snapshot(db_path, conversation_id)
    return {
        "id": scenario["id"],
        "repeat": repeat,
        "steps": steps,
        "before": before,
        "after": after,
    }


def make_kb(name: str, url: str) -> KnowledgeBase:
    return RagProjectKB(url) if name == "rag_project" else FakeKB.from_docs()


def main(
    argv: list[str] | None = None,
    provider_factory: Callable[[Settings, bool], ModelProvider] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="run folder under eval/runs")
    parser.add_argument("--split", choices=["dev", "sealed"], default="dev")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--adversarial-only", action="store_true")
    parser.add_argument("--ablation", default="none", choices=[a.value for a in Ablation])
    parser.add_argument("--think", action="store_true", help="turn on the model's thinking")
    parser.add_argument("--kb", choices=["fake", "rag_project"], default="fake")
    parser.add_argument("--scenarios", type=Path, default=SCENARIO_FILE)
    parser.add_argument("--runs", type=Path, default=RUNS)
    args = parser.parse_args(argv)

    scenarios = load_scenarios(args.scenarios, args.split)
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    if args.adversarial_only:
        scenarios = [s for s in scenarios if s["adversarial"]]
    if not scenarios:
        print("No scenarios match.", file=sys.stderr)
        return 2

    base = Settings()
    folder = args.runs / args.name
    folder.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": args.name,
        "split": args.split,
        "repeats": args.repeats,
        "ablation": args.ablation,
        "thinking": args.think,
        "kb": args.kb,
        "model": base.model,
        "provider": base.model_provider,
        "prompt_version": PROMPT_VERSION,
        "scenario_file": str(args.scenarios),
        "scenario_hash": file_hash(args.scenarios),
        "scenario_ids": [s["id"] for s in scenarios],
        "max_tool_calls_per_turn": base.max_tool_calls_per_turn,
        "turn_timeout_s": base.turn_timeout_s,
        "python": platform.python_version(),
        **git_state(),
    }
    meta_path = folder / "meta.json"
    if meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        for key in ("ablation", "thinking", "kb", "model", "prompt_version", "scenario_hash"):
            if old.get(key) != meta[key]:
                print(
                    f"{meta_path} was made with another {key}; use a new --name.", file=sys.stderr
                )
                return 2
        meta["started_at"] = old["started_at"]
        meta["resumed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    else:
        meta["started_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    results = folder / "results.jsonl"
    done: set[tuple[str, int]] = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                done.add((record["id"], record["repeat"]))

    factory = provider_factory or (
        lambda s, think: OllamaProvider(
            s.ollama_url, s.model, think=think, timeout=s.turn_timeout_s
        )
    )
    provider = factory(base, args.think)
    kb = make_kb(args.kb, base.kb_url)
    todo = [
        (s, r) for r in range(1, args.repeats + 1) for s in scenarios if (s["id"], r) not in done
    ]
    print(f"{len(todo)} runs to do ({len(done)} already in {results}).")
    for number, (scenario, repeat) in enumerate(todo, 1):
        started = time.perf_counter()
        record = run_scenario(scenario, repeat, provider, kb, args.ablation, args.think)
        record["seconds"] = round(time.perf_counter() - started, 1)
        with results.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(
            f"[{number}/{len(todo)}] {scenario['id']} r{repeat} {record['seconds']} s", flush=True
        )
    meta["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
