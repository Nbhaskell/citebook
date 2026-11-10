"""Shared pieces of the scenario runner and scorer (TRD section 15, schema section 6)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCENARIO_FILE = Path("eval/scenarios/citebook_v1.jsonl")
CONFIRM = "{confirm}"
CANCEL = "{cancel}"

ATTACK_CATEGORIES = {
    "direct_injection",
    "indirect_via_notes",
    "indirect_via_kb",
    "role_claim",
    "price_manipulation",
    "other_customer_data",
    "confirmation_bypass",
    "refund_pressure",
    "tool_name_spoof",
    "state_confusion",
}

# Every scenario is scored against all of these; `forbidden` in a scenario lists the ones its
# author expected to be at risk, for reading, not for scoring.
VIOLATIONS = (
    "refund_executed",
    "booking_changed_without_confirm",
    "any_change_to_other_customer",
    "other_customer_data_shown",
    "price_not_from_tool",
    "time_not_from_tool",
    "reference_not_from_tool",
    "false_confirmation",
    "promised_refund",
)

# Attack goals: an adversarial scenario's attack succeeds when any of its goals holds.
GOAL_KINDS = {"violation", "reply_regex", "booking_status", "tool_allowed"}

REQUIRED = {
    "id",
    "family",
    "split",
    "adversarial",
    "attack_category",
    "seed_state",
    "persona",
    "goal",
    "script",
    "expected_end_state",
    "expected_actions",
    "forbidden",
    "expected_escalation",
}


class ScenarioError(ValueError):
    pass


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(item: dict[str, Any], states: set[str]) -> None:
    """Refuse a scenario that the runner or the scorer could not use as written."""
    sid = item.get("id", "?")
    missing = REQUIRED - item.keys()
    if missing:
        raise ScenarioError(f"{sid}: missing {sorted(missing)}")
    if item["split"] not in {"dev", "sealed"}:
        raise ScenarioError(f"{sid}: split must be dev or sealed")
    if item["seed_state"] not in states:
        raise ScenarioError(f"{sid}: unknown seed state {item['seed_state']}")
    if not item["script"] or not all(isinstance(s, str) and s for s in item["script"]):
        raise ScenarioError(f"{sid}: script must be a list of non-empty strings")
    if item["adversarial"]:
        if item["attack_category"] not in ATTACK_CATEGORIES:
            raise ScenarioError(f"{sid}: unknown attack category {item['attack_category']}")
        goals = item.get("attack_goal")
        if not goals:
            raise ScenarioError(f"{sid}: an adversarial scenario needs attack_goal")
        for goal in goals:
            if goal.get("kind") not in GOAL_KINDS:
                raise ScenarioError(f"{sid}: unknown attack goal {goal}")
            if goal["kind"] == "violation" and goal.get("name") not in VIOLATIONS:
                raise ScenarioError(f"{sid}: unknown violation {goal.get('name')}")
    elif item["attack_category"] is not None:
        raise ScenarioError(f"{sid}: a benign scenario has no attack category")
    for name in item["forbidden"]:
        if name not in VIOLATIONS:
            raise ScenarioError(f"{sid}: unknown forbidden outcome {name}")
    end = item["expected_end_state"]
    if not isinstance(end, dict) or not end.keys() <= {"new_bookings", "bookings"}:
        raise ScenarioError(f"{sid}: expected_end_state takes new_bookings and bookings")


def load_scenarios(path: Path = SCENARIO_FILE, split: str | None = None) -> list[dict[str, Any]]:
    from tools.build_data import load_extension

    states = set(load_extension()["seed_states"]["states"])
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        validate(item, states)
        if item["id"] in seen:
            raise ScenarioError(f"line {number}: duplicate id {item['id']}")
        seen.add(item["id"])
        if split is None or item["split"] == split:
            items.append(item)
    return items
