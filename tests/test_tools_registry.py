"""The tool registry against schema section 2.1 and 2.2."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from citebook.models import ActionType, ConversationStatus, ToolKind, ToolName
from citebook.tools.registry import REGISTRY, allowed_in_state, offered_tools

# Schema section 2.1: kind, needs contact, needs verified, creates action.
TABLE = {
    "list_services": ("read", False, False, None),
    "check_availability": ("read", False, False, None),
    "quote_price": ("read", False, False, None),
    "get_policy": ("read", False, False, None),
    "collect_contact": ("read", False, False, None),
    "verify_customer": ("read", False, False, None),
    "find_booking": ("read", False, True, None),
    "propose_hold": ("propose", True, False, "hold_create"),
    "propose_reschedule": ("propose", False, True, "reschedule"),
    "request_cancellation": ("request", False, True, "cancellation"),
    "request_refund": ("request", False, True, "refund_request"),
    "request_exception": ("request", True, False, "policy_exception"),
    "handoff_to_staff": ("request", False, False, None),  # handoffs come from create_handoff
}
TOOLS_DIR = Path(__file__).resolve().parent.parent / "citebook" / "tools"


def test_the_registry_has_every_tool_name_and_nothing_else() -> None:
    assert set(REGISTRY) == {t.value for t in ToolName}
    assert set(TABLE) == set(REGISTRY)


@pytest.mark.parametrize("name", sorted(TABLE))
def test_kind_and_requirements_match_the_schema(name: str) -> None:
    spec = REGISTRY[name]
    kind, contact, verified, creates = TABLE[name]
    assert spec.kind is ToolKind(kind)
    assert spec.needs_contact is contact
    assert spec.needs_verified is verified
    assert spec.creates == (ActionType(creates) if creates else None)
    assert spec.description and spec.args.model_config.get("extra") == "forbid"


def test_no_tool_is_staff_only() -> None:
    assert not any(spec.staff_only for spec in REGISTRY.values())


def test_no_tool_module_can_reach_a_staff_or_confirm_path() -> None:
    """STAFF_ONLY must be unreachable: tools never call confirm, decide, take-over or close."""
    forbidden = {"confirm", "decide", "take_over", "close_conversation", "release_hold"}
    for path in TOOLS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "citebook.actions"
            for alias in node.names
        }
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not (names | attrs) & forbidden, path.name


def test_only_read_tools_take_no_card_and_handoff_needs_no_confirm() -> None:
    for spec in REGISTRY.values():
        if spec.kind is ToolKind.READ:
            assert spec.creates is None


def test_tools_allowed_per_status() -> None:
    def names(status: ConversationStatus) -> set[str]:
        return {n for n, spec in REGISTRY.items() if allowed_in_state(spec, status)}

    assert names(ConversationStatus.ACTIVE) == set(REGISTRY)
    reads = {n for n, row in TABLE.items() if row[0] == "read"}
    assert names(ConversationStatus.AWAITING_CONFIRMATION) == reads | {
        "propose_hold",
        "propose_reschedule",
        "handoff_to_staff",
    }
    for ended in ["handed_off", "closed", "abandoned"]:
        assert names(ConversationStatus(ended)) == set()


def test_offered_schemas_are_function_tools_without_extra_properties() -> None:
    offered = offered_tools(ConversationStatus.ACTIVE)
    assert len(offered) == len(REGISTRY)
    for tool in offered:
        assert tool["type"] == "function"
        assert tool["function"]["parameters"]["additionalProperties"] is False
    assert offered_tools(ConversationStatus.HANDED_OFF) == []
    check = REGISTRY["check_availability"].schema()["function"]["parameters"]
    assert set(check["required"]) == {"service_code", "date_from", "date_to", "zip_code"}
