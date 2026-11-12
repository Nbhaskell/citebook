"""handoff_to_staff: hand the conversation to a person. The only action that needs no Confirm."""

from __future__ import annotations

import re
from typing import Literal

from citebook.actions import create_handoff
from citebook.models import ActionOrigin, EscalationReason, ToolKind, ToolName
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolOutput, ToolSpec, blocked
from citebook.verification import FAILED_SAY

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


# Reasons that a failed verification must not hide: while the customer can still try again,
# they are asked to check the details, and after the last failed try code hands off with
# VERIFICATION_FAILED itself (S-110; D-085).
_WAIT_FOR_VERIFICATION = {"OUT_OF_SERVICE_AREA", "NOT_IN_KNOWLEDGE_BASE", "POLICY_EXCEPTION"}


def _customer_texts(ctx: ToolContext) -> list[str]:
    return [
        row["content"]
        for row in ctx.conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'customer'",
            (ctx.conversation_id,),
        )
    ]


def check_policy(ctx: ToolContext, args: Args) -> Block | None:
    """Reasons that code can check against the conversation (D-085)."""
    conversation = ctx.conversation
    failures = conversation["verification_failures"]
    if (
        args.reason in _WAIT_FOR_VERIFICATION
        and conversation["verification_state"] != "verified"
        and 0 < failures < ctx.settings["verification_max_attempts"]
    ):
        # The model was seen to copy this text to the customer, so it carries the customer's
        # sentence as it is (FAILED_SAY) and says nothing about the customer in the third person.
        return blocked(
            f'Do not hand off yet: the customer can try again. Say: "{FAILED_SAY}" Then call '
            "verify_customer with the details they give.",
            "verification_pending",
        )
    if args.reason == "POLICY_EXCEPTION" and conversation["verified_booking_id"]:
        return blocked(
            "Use request_exception with the customer's request: the customer confirms it on a "
            "card, and then a team member reviews it.",
            "use_request_exception",
        )
    if args.reason == "OUT_OF_SERVICE_AREA":
        texts = _customer_texts(ctx)
        written = {z for text in texts for z in re.findall(r"(?<!\d)\d{5}(?!\d)", text)}
        if texts and not written - set(ctx.settings["zip_areas"]):
            return blocked(
                "The customer has not given a ZIP code outside our service area. Ask for the "
                "ZIP code of the job and check it with check_availability.",
                "no_unserved_zip",
            )
    return None


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
    check_policy=check_policy,
)
