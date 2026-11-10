"""Run one tool call the model proposed: gate it, record it, and run it only if ALLOWED.

Every proposed call, refused or not, becomes a `tool_calls` row with the gate's decision, and an
audit event with ids and codes only. The tool runs in the same transaction as its record, so a
proposal and the tool call it points to are written together or not at all. The model always gets
the schema section 5 envelope back.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from citebook.actions import Rules
from citebook.db import transaction
from citebook.events import audit
from citebook.gate import gate
from citebook.kb import KnowledgeBase
from citebook.models import Ablation, Actor, GateDecision
from citebook.timeutil import to_utc_text
from citebook.tools.base import ToolContext
from citebook.tools.common import load_settings


@dataclass(frozen=True)
class ToolCallResult:
    envelope: dict[str, Any]
    decision: GateDecision
    tool_call_id: str


def run_tool_call(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    turn: int,
    tool_name: str,
    raw_args: Any,
    now: datetime,
    kb: KnowledgeBase,
    ablation: Ablation = Ablation.NONE,
) -> ToolCallResult:
    settings = load_settings(conn)
    with transaction(conn):
        conversation = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise LookupError("unknown conversation")
        ctx = ToolContext(
            conn=conn,
            settings=settings,
            rules=Rules.from_settings(settings),
            kb=kb,
            now=now,
            conversation=conversation,
            ablation=ablation,
        )
        result = gate(ctx, tool_name, raw_args)
        call_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO tool_calls (id, conversation_id, turn, tool, arguments, gate_decision,"
            " gate_detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                call_id,
                conversation_id,
                turn,
                str(tool_name)[:100],
                json.dumps(raw_args, default=str, ensure_ascii=False),
                result.decision.value,
                json.dumps(result.detail) if result.detail else None,
                to_utc_text(now),
            ),
        )
        safe_name = result.spec.name.value if result.spec else "unknown"
        audit(
            conn,
            Actor.ASSISTANT,
            "tool_gated",
            conversation_id=conversation_id,
            tool=safe_name,
            gate_decision=result.decision.value,
            tool_call_id=call_id,
        )
        if not result.allowed:
            refusal = {
                "ok": False,
                "tool": safe_name,
                "gate_decision": result.decision.value,
                "say": result.say,
            }
            return ToolCallResult(refusal, result.decision, call_id)

        assert result.spec is not None and result.args is not None
        ctx.tool_call_id = call_id
        started = time.perf_counter()
        output = result.spec.handler(ctx, result.args)
        duration_ms = int((time.perf_counter() - started) * 1000)
        envelope: dict[str, Any]
        if output.ok:
            envelope = {
                "ok": True,
                "tool": safe_name,
                "data": output.data,
                "untrusted_fields": output.untrusted_fields,
            }
        else:
            envelope = {"ok": False, "tool": safe_name, "error": output.error, "say": output.say}
        conn.execute(
            "UPDATE tool_calls SET result = ?, duration_ms = ? WHERE id = ?",
            (json.dumps(envelope, ensure_ascii=False), duration_ms, call_id),
        )
        audit(
            conn,
            Actor.SYSTEM,
            "tool_ran",
            conversation_id=conversation_id,
            tool=safe_name,
            ok=output.ok,
            tool_call_id=call_id,
            duration_ms=duration_ms,
        )
    return ToolCallResult(envelope, result.decision, call_id)
