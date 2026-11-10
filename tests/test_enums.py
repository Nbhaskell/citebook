"""Code enums, database CHECK constraints and BACKEND_SCHEMA.md section 2 use the same strings."""

import re
from enum import StrEnum
from pathlib import Path

import pytest

from citebook import models
from citebook.db import MIGRATIONS_DIR

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "BACKEND_SCHEMA.md").read_text(encoding="utf-8")
SQL = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS_DIR.glob("*.sql")))


def doc_enums() -> dict[str, list[str]]:
    table = DOC.split("## 2. Enums", 1)[1].split("### 2.1", 1)[0]
    found: dict[str, list[str]] = {}
    for name, values in re.findall(r"^\| `(\w+)` \| (.+) \|$", table, re.M):
        found[name] = re.findall(r"`([^`]+)`", values)
    return found


def check_values(table: str, column: str) -> list[str]:
    create = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", SQL, re.S)
    assert create, table
    match = re.search(rf"\b{column}\b[^\n]*?CHECK \({column} IN \(([^)]*)\)\)", create.group(1))
    assert match, f"{table}.{column}"
    return re.findall(r"'([^']+)'", match.group(1))


# Enum name in the schema document -> the StrEnum in citebook/models.py.
ENUMS: dict[str, type[StrEnum]] = {
    "conversation_status": models.ConversationStatus,
    "channel": models.Channel,
    "message_role": models.MessageRole,
    "verification_state": models.VerificationState,
    "pricing_kind": models.PricingKind,
    "service_zone": models.ServiceZone,
    "action_origin": models.ActionOrigin,
    "slot_status": models.SlotStatus,
    "booking_status": models.BookingStatus,
    "action_type": models.ActionType,
    "action_status": models.ActionStatus,
    "tool_name": models.ToolName,
    "tool_kind": models.ToolKind,
    "gate_decision": models.GateDecision,
    "reply_check_code": models.ReplyCheckCode,
    "screen_flag": models.ScreenFlag,
    "escalation_reason": models.EscalationReason,
    "staff_decision": models.StaffDecision,
    "provider": models.Provider,
    "actor": models.Actor,
    "outbox_kind": models.OutboxKind,
    "ablation": models.Ablation,
}

# Database columns with a CHECK constraint -> the StrEnum they hold.
COLUMNS: list[tuple[str, str, type[StrEnum]]] = [
    ("conversations", "channel", models.Channel),
    ("conversations", "status", models.ConversationStatus),
    ("conversations", "verification_state", models.VerificationState),
    ("messages", "role", models.MessageRole),
    ("services", "pricing_kind", models.PricingKind),
    ("slots", "status", models.SlotStatus),
    ("bookings", "status", models.BookingStatus),
    ("screen_flags", "flag", models.ScreenFlag),
    ("tool_calls", "gate_decision", models.GateDecision),
    ("actions", "origin", models.ActionOrigin),
    ("actions", "action_type", models.ActionType),
    ("actions", "status", models.ActionStatus),
    ("actions", "escalation_reason", models.EscalationReason),
    ("staff_decisions", "decision", models.StaffDecision),
    ("reply_checks", "check_code", models.ReplyCheckCode),
    ("outbox", "kind", models.OutboxKind),
    ("audit_events", "actor", models.Actor),
]


def test_every_documented_enum_has_a_code_enum() -> None:
    documented = {k for k, v in doc_enums().items() if k != "service_code"}
    assert documented == set(ENUMS)


@pytest.mark.parametrize("name", sorted(ENUMS))
def test_code_enum_matches_the_document(name: str) -> None:
    assert [e.value for e in ENUMS[name]] == doc_enums()[name]


@pytest.mark.parametrize(("table", "column", "enum"), COLUMNS, ids=lambda x: str(x))
def test_check_constraint_matches_the_code_enum(
    table: str, column: str, enum: type[StrEnum]
) -> None:
    assert check_values(table, column) == [e.value for e in enum]


def test_every_check_constraint_is_covered() -> None:
    listed = {(t, c) for t, c, _ in COLUMNS}
    found = set()
    for table, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", SQL, re.S):
        for column in re.findall(r"CHECK \((\w+) IN \('", body):
            found.add((table, column))
    assert found == listed
