"""The tool registry: every tool in `tool_name`, and which tools each conversation status allows.

Least privilege (TRD section 9, layer 1): the turn loop offers only `offered_tools(status)`, and
the gate refuses anything else with NOT_ALLOWED_IN_STATE.
"""

from __future__ import annotations

from typing import Any

from citebook.models import ConversationStatus
from citebook.tools import (
    check_availability,
    collect_contact,
    find_booking,
    get_policy,
    handoff_to_staff,
    list_services,
    propose_hold,
    propose_reschedule,
    quote_price,
    request_cancellation,
    request_exception,
    request_refund,
    verify_customer,
)
from citebook.tools.base import ToolSpec

REGISTRY: dict[str, ToolSpec] = {
    module.SPEC.name.value: module.SPEC
    for module in (
        list_services,
        check_availability,
        quote_price,
        get_policy,
        collect_contact,
        verify_customer,
        find_booking,
        propose_hold,
        propose_reschedule,
        request_cancellation,
        request_refund,
        request_exception,
        handoff_to_staff,
    )
}


def allowed_in_state(spec: ToolSpec, status: ConversationStatus) -> bool:
    """Schema section 2.2. While a card waits, every tool is allowed: a new `propose` or
    `request` withdraws the open card first, and the new card still needs Confirm (D-056)."""
    return status in {ConversationStatus.ACTIVE, ConversationStatus.AWAITING_CONFIRMATION}


def offered_tools(status: ConversationStatus) -> list[dict[str, Any]]:
    """Tool schemas for the model, only those allowed in this status."""
    return [spec.schema() for spec in REGISTRY.values() if allowed_in_state(spec, status)]
