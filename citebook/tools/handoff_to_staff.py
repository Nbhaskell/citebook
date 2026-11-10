"""handoff_to_staff: hand the conversation to a person. The only action that needs no Confirm."""

from __future__ import annotations

from typing import Literal

from citebook.actions import create_handoff
from citebook.models import ActionOrigin, EscalationReason, ToolKind, ToolName
from citebook.tools.base import ToolArgs, ToolContext, ToolOutput, ToolSpec

# Reasons the model may give. Emergencies come from screening, and failures from the turn loop.
ToolReason = Literal[
    "CUSTOMER_ASKED_FOR_PERSON",
    "OUT_OF_SERVICE_AREA",
    "NOT_IN_KNOWLEDGE_BASE",
    "POLICY_EXCEPTION",
    "SUSPICIOUS_INPUT",
]


class Args(ToolArgs):
    reason: ToolReason = "CUSTOMER_ASKED_FOR_PERSON"


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    assert ctx.tool_call_id is not None
    action_id = create_handoff(
        ctx.conn,
        conversation_id=ctx.conversation_id,
        origin=ActionOrigin.TOOL,
        reason=EscalationReason(args.reason),
        now=ctx.now,
        tool_call_id=ctx.tool_call_id,
    )
    return ToolOutput(
        data={
            "handed_off": True,
            "action_id": action_id,
            "next": "Tell the customer a team member will reply here.",
        }
    )


SPEC = ToolSpec(
    name=ToolName.HANDOFF_TO_STAFF,
    kind=ToolKind.REQUEST,
    args=Args,
    description=(
        "Hand the conversation to a team member. Use it when the customer asks for a person, "
        "the ZIP code is not served, the knowledge base has no answer, or a request is outside "
        "policy."
    ),
    handler=handle,
)
