"""Reply checks (TRD section 8): code checks every assistant draft before the customer sees it.

`check_reply(reply, allowed)` is pure. `build_allowed(...)` collects what this conversation may
say: figures from its tool results, settings and the price book, the dates in this turn's STATE
block, the customer's own booking references, and the other customers' details it must never
show. Every check runs on every draft and reports what it found and what it compared with.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from citebook.models import ReplyCheckCode
from citebook.money import format_money
from citebook.numbers import find_dates, find_money, find_policy_numbers, find_times

C = ReplyCheckCode
REFERENCE = re.compile(r"\bCB-[A-Z0-9]{6}\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class OtherCustomer:
    names: tuple[str, ...]
    phone_digits: str
    email: str
    street: str


@dataclass
class Allowed:
    money: set[int] = field(default_factory=set)
    times: set[int] = field(default_factory=set)
    dates: set[tuple[int, int]] = field(default_factory=set)
    policy: set[tuple[int, str]] = field(default_factory=set)
    references: set[str] = field(default_factory=set)
    other_customers: list[OtherCustomer] = field(default_factory=list)
    other_references: set[str] = field(default_factory=set)
    done: set[str] = field(default_factory=set)  # "confirmed", "cancelled", "moved" shown as true
    safety_texts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CheckResult:
    code: ReplyCheckCode
    passed: bool
    detail: dict[str, Any]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


# --- the eight checks -------------------------------------------------------------------------


def price_from_tool(reply: str, allowed: Allowed) -> CheckResult:
    found = find_money(reply)
    unmatched = [f.raw for f in found if f.value not in allowed.money]
    return CheckResult(
        C.PRICE_FROM_TOOL,
        not unmatched,
        {
            "found_in_reply": [f.raw for f in found],
            "allowed": [format_money(m) for m in sorted(allowed.money)],
            "unmatched": unmatched,
        },
    )


def time_from_tool(reply: str, allowed: Allowed) -> CheckResult:
    times = find_times(reply)
    dates = find_dates(reply)
    unmatched = [f.raw for f in times if not (f.value & allowed.times)]
    unmatched += [f.raw for f in dates if f.value not in allowed.dates]
    return CheckResult(
        C.TIME_FROM_TOOL,
        not unmatched,
        {"found_in_reply": [f.raw for f in times + dates], "unmatched": unmatched},
    )


def reference_from_tool(reply: str, allowed: Allowed) -> CheckResult:
    found = [m.upper() for m in REFERENCE.findall(reply)]
    unmatched = [r for r in found if r not in allowed.references]
    return CheckResult(
        C.REFERENCE_FROM_TOOL, not unmatched, {"found_in_reply": found, "unmatched": unmatched}
    )


def policy_grounded(reply: str, allowed: Allowed) -> CheckResult:
    found = find_policy_numbers(reply)
    unmatched = [f.raw for f in found if f.value not in allowed.policy]
    return CheckResult(
        C.POLICY_GROUNDED,
        not unmatched,
        {"found_in_reply": [f.raw for f in found], "unmatched": unmatched},
    )


_HEDGE = re.compile(
    r"\b(not|never|nothing|until|once|when|after|before|if|will|would|can|could|may|might|yet|"
    r"press|pressing|tap|request|requested)\b|n't\b",
    re.IGNORECASE,
)
_CLAIMS = {
    "confirmed": re.compile(
        r"\b(?:is|are|been|now|all|you're|you are|i've|i have|we've|we have|has|have)\s+"
        r"(?:all\s+|now\s+|been\s+)?(?:booked|confirmed|scheduled)\b|\bbooked you\b",
        re.IGNORECASE,
    ),
    "cancelled": re.compile(
        r"\b(?:is|are|been|now|i've|i have|we've|we have|has|have)\s+(?:now\s+|been\s+)?"
        r"cancell?ed\b",
        re.IGNORECASE,
    ),
    "moved": re.compile(
        r"\b(?:is|are|been|now|i've|i have|we've|we have|has|have)\s+(?:now\s+|been\s+)?"
        r"(?:moved|rescheduled)\b",
        re.IGNORECASE,
    ),
}


def no_false_confirmation(reply: str, allowed: Allowed) -> CheckResult:
    claims = []
    for sentence in _sentences(reply):
        if _HEDGE.search(sentence):
            continue
        for kind, pattern in _CLAIMS.items():
            if pattern.search(sentence) and kind not in allowed.done:
                claims.append(kind)
    return CheckResult(
        C.NO_FALSE_CONFIRMATION,
        not claims,
        {"claims": claims, "true_in_this_conversation": sorted(allowed.done)},
    )


def no_other_customer_data(reply: str, allowed: Allowed) -> CheckResult:
    lowered = reply.lower()
    digits = {re.sub(r"\D", "", m) for m in re.findall(r"\d[\d\s().-]{5,}\d", reply)}
    found = []
    for other in allowed.other_customers:
        if any(re.search(rf"\b{re.escape(n.lower())}\b", lowered) for n in other.names):
            found.append("name")
        if other.phone_digits and any(d.endswith(other.phone_digits[-7:]) for d in digits):
            found.append("phone")
        if other.email and other.email in lowered:
            found.append("email")
        if len(other.street) >= 6 and other.street in lowered:
            found.append("address")
    found += [
        "reference" for r in REFERENCE.findall(reply) if r.upper() in allowed.other_references
    ]
    return CheckResult(C.NO_OTHER_CUSTOMER_DATA, not found, {"kinds_found": sorted(set(found))})


_MONEY_BACK = re.compile(
    r"\b(refund\w*|money back|reimburs\w*|waiv\w*|credit (?:you|your|it back|back)|"
    r"(?:fee|charge) (?:is |will be )?(?:dropped|removed)|no (?:late )?fee)\b",
    re.IGNORECASE,
)
_PROMISE = re.compile(
    r"\b(you'll|you will|we'll|we will|i'll|i will|will be|is being|has been|i've|we've|"
    r"going to|get|receive|issued|approved)\b",
    re.IGNORECASE,
)
_NO_PROMISE = re.compile(
    r"\b(cannot|can't|not|never|no one|review\w*|request\w*|decide\w*|team|staff|person|"
    r"policy)\b|n't\b",
    re.IGNORECASE,
)


def no_promised_refund(reply: str, allowed: Allowed) -> CheckResult:
    promises = [
        s
        for s in _sentences(reply)
        if _MONEY_BACK.search(s) and _PROMISE.search(s) and not _NO_PROMISE.search(s)
    ]
    return CheckResult(C.NO_PROMISED_REFUND, not promises, {"sentences": len(promises)})


_RISKY_SUBJECT = re.compile(
    r"\b(gas (?:line|valve|pipe|meter|supply|leak)|pilot light|burner|breaker|circuit|wiring|"
    r"wires?|outlet|electrical|fuse|panel)\b",
    re.IGNORECASE,
)
_INSTRUCTION = re.compile(
    r"^(?:please\s+)?(?:turn|shut|close|open|check|relight|light|reset|flip|tighten|loosen|"
    r"disconnect|replace|rewire|unplug|remove|try|switch)\b|\b(?:you can|you could|you should|"
    r"you need to|try to|try)\b",
    re.IGNORECASE,
)


def scope(reply: str, allowed: Allowed) -> CheckResult:
    risky = []
    for sentence in _sentences(reply):
        if any(sentence.rstrip(".") in text for text in allowed.safety_texts):
            continue
        if _RISKY_SUBJECT.search(sentence) and _INSTRUCTION.search(sentence):
            risky.append(sentence[:40])
    return CheckResult(C.SCOPE, not risky, {"sentences": len(risky)})


CHECKS = [
    price_from_tool,
    time_from_tool,
    reference_from_tool,
    policy_grounded,
    no_false_confirmation,
    no_other_customer_data,
    no_promised_refund,
    scope,
]


def check_reply(reply: str, allowed: Allowed) -> list[CheckResult]:
    return [check(reply, allowed) for check in CHECKS]


def feedback(results: list[CheckResult]) -> str:
    """What the model is told when its draft failed, for one regeneration."""
    hints = {
        C.PRICE_FROM_TOOL: "Use only amounts that appear in tool results; call quote_price.",
        C.TIME_FROM_TOOL: "Use only dates and times from tool results or the STATE block.",
        C.REFERENCE_FROM_TOOL: "Mention only this customer's booking references.",
        C.POLICY_GROUNDED: "State limits and time periods only as get_policy passages give them.",
        C.NO_FALSE_CONFIRMATION: "Do not say a booking is booked, confirmed, cancelled or moved; "
        "a held booking is held until our team confirms it.",
        C.NO_OTHER_CUSTOMER_DATA: "Never mention other customers or their bookings.",
        C.NO_PROMISED_REFUND: "Do not promise a refund, credit or waived fee; a team member "
        "reviews requests.",
        C.SCOPE: "Do not give instructions for gas or electrical work.",
    }
    lines = ["Your last draft was not sent. Write it again and fix these points:"]
    for result in results:
        if not result.passed:
            unmatched = result.detail.get("unmatched")
            extra = f" Not allowed here: {', '.join(unmatched)}." if unmatched else ""
            lines.append(f"- {result.code.value}: {hints[result.code]}{extra}")
    return "\n".join(lines)


# --- what this conversation may say ----------------------------------------------------------


def _add_text(allowed: Allowed, text: str) -> None:
    allowed.money.update(f.value for f in find_money(text))
    for f in find_times(text):
        allowed.times.update(f.value)
    allowed.dates.update(f.value for f in find_dates(text))
    allowed.policy.update(f.value for f in find_policy_numbers(text))


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def build_allowed(
    conn: sqlite3.Connection,
    conversation_id: str,
    settings: dict[str, Any],
    book: dict[str, Any],
    state_text: str,
    safety: dict[str, Any],
) -> Allowed:
    allowed = Allowed()
    for row in conn.execute(
        "SELECT result FROM tool_calls WHERE conversation_id = ? AND result IS NOT NULL",
        (conversation_id,),
    ):
        _add_text(allowed, row["result"])
        if '"status": "confirmed"' in row["result"]:
            allowed.done.add("confirmed")
        if '"status": "cancelled"' in row["result"]:
            allowed.done.add("cancelled")
    _add_text(allowed, state_text)

    allowed.money.update(
        abs(v) for k, v in book.items() if k.endswith("_minor") and isinstance(v, int)
    )
    for span in settings["office_hours"].values():
        if isinstance(span, dict):
            allowed.times.update({_minutes(span["open"]), _minutes(span["close"])})
    window = settings["arrival_window_minutes"]
    for starts in settings["visit_hours"].values():
        for start in starts:
            allowed.times.update({_minutes(start), _minutes(start) + window})
    allowed.policy.update(
        {
            (settings["free_cancel_hours"], "hours"),
            (settings["hold_ttl_business_hours"], "hours"),
            (settings["staff_confirm_typical_business_hours"], "hours"),
            (settings["quote_ttl_minutes"], "minutes"),
            (settings["max_reschedules"], "times"),
            (settings["verification_max_attempts"], "attempts"),
            (window, "minutes"),
            (window // 60, "hours"),
        }
    )

    conversation = conn.execute(
        "SELECT customer_id FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    customer_id = conversation["customer_id"] if conversation else None
    for row in conn.execute("SELECT reference, customer_id FROM bookings"):
        if customer_id and row["customer_id"] == customer_id:
            allowed.references.add(row["reference"])
        else:
            allowed.other_references.add(row["reference"])
    for row in conn.execute("SELECT * FROM customers WHERE id IS NOT ?", (customer_id,)):
        has_booking = conn.execute(
            "SELECT 1 FROM bookings WHERE customer_id = ?", (row["id"],)
        ).fetchone()
        if not has_booking:
            continue  # contact rows of other chats; a name alone there is too common to check
        allowed.other_customers.append(
            OtherCustomer(
                names=(row["name"],),
                phone_digits=re.sub(r"\D", "", row["phone"]),
                email=(row["email"] or "").lower(),
                street=row["address"].split(",")[0].strip().lower(),
            )
        )
    for kind, done_word in [("hold_create", "confirmed"), ("cancellation", "cancelled")]:
        if conn.execute(
            "SELECT 1 FROM actions WHERE conversation_id = ? AND action_type = ?"
            " AND status = 'executed'",
            (conversation_id, kind),
        ).fetchone():
            allowed.done.add(done_word)
    if conn.execute(
        "SELECT 1 FROM actions WHERE conversation_id = ? AND action_type = 'reschedule'"
        " AND status = 'executed'",
        (conversation_id,),
    ).fetchone():
        allowed.done.add("moved")
    allowed.safety_texts = [*safety["instructions"].values()]
    _add_text(allowed, json.dumps(safety))
    return allowed
