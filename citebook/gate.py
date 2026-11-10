"""The policy gate (TRD section 5): code decides whether a tool call the model proposed may run.

Checks run in this order and the first failure wins:

1. UNKNOWN_TOOL          the name is not in the registry
2. NOT_ALLOWED_IN_STATE  the conversation status does not allow the tool (schema 2.2)
3. INVALID_ARGUMENTS     Pydantic validation with extra fields refused; ids must exist and belong
                         to this conversation; a booking reference must be the verified booking
4. NEEDS_CONTACT         the tool needs contact details and none are collected
5. NEEDS_VERIFICATION    the tool needs a verified booking
6. RATE_LIMITED          per-conversation limits
7. POLICY_BLOCKED        business rules
8. STAFF_ONLY            reserved: no tool can reach it (a test asserts that)

Identity comes from the conversation record, never from the arguments. Each refusal carries a
short `say` hint written by code. The `baseline` and `no_gate_policy` ablations (evaluation only,
TRD section 9) skip checks 2 and 4-7 and the ownership check; checks 1 and 3 always run, because
a call that fails them cannot run at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from citebook.models import Ablation, ConversationStatus, GateDecision
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolSpec
from citebook.tools.registry import REGISTRY, allowed_in_state

WEAKENED = {Ablation.BASELINE, Ablation.NO_GATE_POLICY}

SAY_UNKNOWN_TOOL = "There is no tool with that name. Use only the tools you are offered."
SAY_STATE = {  # `active` and `awaiting_confirmation` allow every tool (D-056)
    ConversationStatus.HANDED_OFF: "A team member has this conversation. Do not call tools.",
    ConversationStatus.CLOSED: "This conversation has ended.",
    ConversationStatus.ABANDONED: "This conversation has ended.",
}
SAY_NEEDS_CONTACT = (
    "Collect the customer's name, phone number, service address and ZIP code with "
    "collect_contact first."
)
SAY_NEEDS_VERIFICATION = (
    "Ask for the booking reference and the last 4 digits of the phone number on the booking, "
    "then call verify_customer."
)
SAY_RATE_LIMITED = (
    "This tool has been used too many times in this conversation. Offer a person "
    "(handoff_to_staff)."
)
SAY_STAFF_ONLY = "Only a team member can do that."


@dataclass(frozen=True)
class GateResult:
    decision: GateDecision
    say: str | None = None
    detail: dict[str, Any] | None = None
    spec: ToolSpec | None = None
    args: ToolArgs | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is GateDecision.ALLOWED


def _refuse(block: Block, spec: ToolSpec | None = None, **detail: Any) -> GateResult:
    info = {"check": block.detail, **detail} if block.detail else detail
    return GateResult(block.decision, block.say, info or None, spec)


# --- one function per check -------------------------------------------------------------------


def check_known(tool_name: str) -> ToolSpec | Block:
    spec = REGISTRY.get(tool_name)
    if spec is None:
        return Block(GateDecision.UNKNOWN_TOOL, SAY_UNKNOWN_TOOL, "unknown_tool")
    return spec


def check_state(ctx: ToolContext, spec: ToolSpec) -> Block | None:
    status = ConversationStatus(ctx.conversation["status"])
    if allowed_in_state(spec, status):
        return None
    return Block(GateDecision.NOT_ALLOWED_IN_STATE, SAY_STATE[status], status.value)


def _say_for(error: ValidationError) -> str:
    """Name the fields and the problem; never repeat the value, which may be customer text."""
    missing, extra, other = [], [], []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"]) or "arguments"
        if item["type"] == "missing":
            missing.append(where)
        elif item["type"] == "extra_forbidden":
            extra.append(where)
        else:
            message = str(item["msg"]).removeprefix("Value error, ")
            other.append(f"{where}: {message}")
    parts = []
    if missing:
        parts.append(f"Missing: {', '.join(missing)}. Ask the customer for it.")
    if extra:
        parts.append(f"Remove these arguments: {', '.join(extra)}.")
    if other:
        parts.append(f"Fix {'; '.join(other[:3])}.")
    return " ".join(parts)


def check_arguments(spec: ToolSpec, raw_args: Any) -> ToolArgs | Block:
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        return Block(
            GateDecision.INVALID_ARGUMENTS, "Arguments must be a JSON object.", "not_an_object"
        )
    try:
        return spec.args.model_validate(raw_args)
    except ValidationError as error:
        return Block(GateDecision.INVALID_ARGUMENTS, _say_for(error), "validation")


def check_contact(ctx: ToolContext, spec: ToolSpec) -> Block | None:
    if not spec.needs_contact:
        return None
    state = ctx.conversation["verification_state"]
    if state in {"contact_collected", "verified"} and ctx.conversation["customer_id"]:
        return None
    return Block(GateDecision.NEEDS_CONTACT, SAY_NEEDS_CONTACT, "needs_contact")


def check_verified(ctx: ToolContext, spec: ToolSpec) -> Block | None:
    if not spec.needs_verified:
        return None
    conv = ctx.conversation
    if conv["verification_state"] == "verified" and conv["verified_booking_id"]:
        return None
    return Block(GateDecision.NEEDS_VERIFICATION, SAY_NEEDS_VERIFICATION, "needs_verification")


def check_rate(ctx: ToolContext, spec: ToolSpec, args: ToolArgs) -> Block | None:
    if spec.check_rate is not None:
        block = spec.check_rate(ctx, args)
        if block is not None:
            return block
    if spec.rate_limit is None:
        return None
    used = ctx.conn.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE conversation_id = ? AND tool = ?"
        " AND gate_decision = 'ALLOWED'",
        (ctx.conversation_id, spec.name.value),
    ).fetchone()[0]
    if used >= spec.rate_limit:
        return Block(GateDecision.RATE_LIMITED, SAY_RATE_LIMITED, "call_limit")
    return None


def check_staff_only(spec: ToolSpec) -> Block | None:
    if spec.staff_only:
        return Block(GateDecision.STAFF_ONLY, SAY_STAFF_ONLY, "staff_only")
    return None


# --- the gate ---------------------------------------------------------------------------------


def gate(ctx: ToolContext, tool_name: str, raw_args: Any) -> GateResult:
    """Decide one proposed tool call. Reads the database; writes nothing."""
    known = check_known(tool_name)
    if isinstance(known, Block):
        return _refuse(known)
    spec = known
    full = ctx.ablation not in WEAKENED

    if full and (block := check_state(ctx, spec)):
        return _refuse(block, spec)

    parsed = check_arguments(spec, raw_args)
    if isinstance(parsed, Block):
        return _refuse(parsed, spec)
    args = parsed
    if spec.check_ids and (block := spec.check_ids(ctx, args)):
        return _refuse(block, spec)
    if full and spec.check_owner and (block := spec.check_owner(ctx, args)):
        return _refuse(block, spec)

    if full:
        later_checks: list[Callable[[], Block | None]] = [
            lambda: check_contact(ctx, spec),
            lambda: check_verified(ctx, spec),
            lambda: check_rate(ctx, spec, args),
            lambda: spec.check_policy(ctx, args) if spec.check_policy else None,
        ]
        for run_check in later_checks:  # in order; a later check may rely on an earlier one
            if block := run_check():
                return _refuse(block, spec)

    if block := check_staff_only(spec):
        return _refuse(block, spec)
    return GateResult(GateDecision.ALLOWED, spec=spec, args=args)
