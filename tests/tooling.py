"""Helpers for driving tools through the gate, as the turn loop will, with no model."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from citebook.kb import FakeKB
from citebook.models import Ablation
from citebook.tools.runner import run_tool_call
from tests.conftest import local

NOW = local("2026-11-09T08:00")  # Monday, base_week_1
KB = FakeKB.from_docs()
SAM = {"reference": "CB-7KQ4MX", "phone_last4": "0142"}  # Tuesday 17 November, 10:00 AM
PRIYA = {"reference": "CB-3HTN8R", "phone_last4": "0103"}
CONTACT = {
    "name": "Alex Rivera",
    "phone": "555-0150",
    "address": "9 Example St",
    "zip_code": "00012",
}


def call(
    conn: sqlite3.Connection,
    conversation_id: str,
    tool: str,
    args: Any = None,
    now: datetime = NOW,
    ablation: Ablation = Ablation.NONE,
    kb: FakeKB = KB,
) -> dict[str, Any]:
    """One tool call through the gate; returns the envelope the model would see."""
    result = run_tool_call(
        conn,
        conversation_id=conversation_id,
        turn=1,
        tool_name=tool,
        raw_args={} if args is None else args,
        now=now,
        kb=kb,
        ablation=ablation,
    )
    return result.envelope


def ok(envelope: dict[str, Any]) -> dict[str, Any]:
    assert envelope["ok"], envelope
    data: dict[str, Any] = envelope["data"]
    return data


def decision(envelope: dict[str, Any]) -> str:
    assert not envelope["ok"], envelope
    return str(envelope.get("gate_decision"))


def first_slot(
    conn: sqlite3.Connection,
    conversation_id: str,
    service: str = "FAUCET_REPLACEMENT",
    date_from: str = "2026-11-10",
    zip_code: str = "00012",
    now: datetime = NOW,
    index: int = 0,
) -> str:
    data = ok(
        call(
            conn,
            conversation_id,
            "check_availability",
            {
                "service_code": service,
                "date_from": date_from,
                "date_to": date_from,
                "zip_code": zip_code,
            },
            now,
        )
    )
    slot_id: str = data["slots"][index]["slot_id"]
    return slot_id


def quote_for(
    conn: sqlite3.Connection,
    conversation_id: str,
    slot_id: str,
    service: str = "FAUCET_REPLACEMENT",
    zip_code: str = "00012",
    now: datetime = NOW,
) -> dict[str, Any]:
    return ok(
        call(
            conn,
            conversation_id,
            "quote_price",
            {"service_code": service, "zip_code": zip_code, "slot_id": slot_id},
            now,
        )
    )
