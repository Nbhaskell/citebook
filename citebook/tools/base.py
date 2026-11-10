"""What every tool declares, and what a tool handler receives and returns (TRD section 6).

A tool declares its name, kind, requirements, Pydantic argument model, description for the model
and handler, plus optional checks that the gate runs: `check_ids` (ids exist and belong to this
conversation), `check_owner` (the booking reference is the verified booking) and `check_policy`
(business rules). Handlers never take customer identity from arguments: it comes from the
conversation record in `ToolContext`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from citebook.actions import Rules
from citebook.kb import KnowledgeBase
from citebook.models import Ablation, ActionType, GateDecision, ToolKind, ToolName


class ToolArgs(BaseModel):
    """Base for argument models: unknown arguments are refused, text is trimmed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoArgs(ToolArgs):
    pass


@dataclass
class ToolContext:
    conn: sqlite3.Connection
    settings: dict[str, Any]
    rules: Rules
    kb: KnowledgeBase
    now: datetime
    conversation: sqlite3.Row
    ablation: Ablation = Ablation.NONE
    tool_call_id: str | None = None

    @property
    def conversation_id(self) -> str:
        cid: str = self.conversation["id"]
        return cid


@dataclass(frozen=True)
class Block:
    """A gate refusal: the decision code and a short hint for the model."""

    decision: GateDecision
    say: str
    detail: str = ""


@dataclass(frozen=True)
class ToolOutput:
    """What a handler returns. `ok = False` only when the tool's own SQL found nothing it may
    touch (the second ownership layer); gate refusals never reach a handler."""

    data: dict[str, Any] = field(default_factory=dict)
    untrusted_fields: list[str] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    say: str | None = None


Check = Callable[[ToolContext, Any], Block | None]
Handler = Callable[[ToolContext, Any], ToolOutput]


@dataclass(frozen=True)
class ToolSpec:
    name: ToolName
    kind: ToolKind
    args: type[ToolArgs]
    description: str
    handler: Handler
    needs_contact: bool = False
    needs_verified: bool = False
    creates: ActionType | None = None
    rate_limit: int | None = None
    check_ids: Check | None = None
    check_owner: Check | None = None
    check_rate: Check | None = None  # a per-tool limit other than a call count
    check_policy: Check | None = None
    staff_only: bool = False  # no tool may be; a test asserts it (TRD section 5, check 8)

    def schema(self) -> dict[str, Any]:
        """The tool as offered to the model (JSON Schema from the argument model)."""
        parameters = self.args.model_json_schema()
        parameters.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name.value,
                "description": self.description,
                "parameters": parameters,
            },
        }


def blocked(say: str, detail: str = "") -> Block:
    return Block(GateDecision.POLICY_BLOCKED, say, detail)


def invalid(say: str, detail: str = "") -> Block:
    return Block(GateDecision.INVALID_ARGUMENTS, say, detail)
