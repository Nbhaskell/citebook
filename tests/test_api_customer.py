"""Customer API routes: session tokens, messages, and Confirm bound to the card's payload hash."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from citebook.api import create_app, token_hash
from citebook.config import Settings
from citebook.db import connect
from citebook.providers.fake import FakeProvider, say, use
from tests.conftest import seeded
from tests.test_loop import _hold, _pick_slot, _quote_text
from tests.tooling import CONTACT, KB, NOW


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / "citebook.db"
        seeded(str(self.path)).close()
        self.fake = FakeProvider()
        self.now = NOW
        settings = Settings(_env_file=None, CITEBOOK_DB_PATH=str(self.path))
        app = create_app(settings, provider=self.fake, kb=KB, clock=lambda: self.now)
        self.client = TestClient(app)

    def db(self) -> sqlite3.Connection:
        return connect(self.path)

    def start(self) -> tuple[str, dict[str, str]]:
        body = self.client.post("/api/conversations", json={"channel": "simulator"}).json()
        return body["conversation_id"], {"X-Session-Token": body["session_token"]}

    def say(self, conversation: str, headers: dict[str, str], text: str, *script: Any) -> Any:
        self.fake.script.extend(script)
        response = self.client.post(
            f"/api/conversations/{conversation}/messages", json={"text": text}, headers=headers
        )
        assert self.fake.script == []
        return response


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    yield World(tmp_path)


def card_for(world: World) -> tuple[str, dict[str, str], dict[str, Any]]:
    conversation, headers = world.start()
    world.say(
        conversation,
        headers,
        "Drain cleaning Thursday, 00012",
        use(
            "check_availability",
            service_code="DRAIN_CLEANING",
            date_from="2026-11-12",
            date_to="2026-11-12",
            zip_code="00012",
        ),
        _pick_slot,
        _quote_text,
    )
    response = world.say(
        conversation,
        headers,
        "Book it, Alex Rivera 555-0150 9 Example St 00012",
        use("collect_contact", **CONTACT),
        _hold,
        say("Please check the card and press Confirm."),
    )
    body = response.json()
    assert response.status_code == 200 and body["card"], body
    return conversation, headers, body["card"]


def test_a_conversation_gets_a_token_that_is_stored_only_as_a_hash(world: World) -> None:
    response = world.client.post("/api/conversations")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "active" and body["synthetic_data"] is True
    row = (
        world.db()
        .execute("SELECT * FROM conversations WHERE id = ?", (body["conversation_id"],))
        .fetchone()
    )
    assert row["channel"] == "web_widget"
    assert row["session_token_hash"] == token_hash(body["session_token"])
    assert body["session_token"] not in str(dict(row))


@pytest.mark.parametrize("token", [None, "", "wrong"])
def test_without_the_right_token_the_conversation_does_not_exist(
    world: World, token: str | None
) -> None:
    conversation, _ = world.start()
    headers = {} if token is None else {"X-Session-Token": token}
    response = world.client.post(
        f"/api/conversations/{conversation}/messages", json={"text": "hi"}, headers=headers
    )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND" and error["request_id"]
    unknown = world.client.post(
        "/api/conversations/nope/messages", json={"text": "hi"}, headers={"X-Session-Token": "x"}
    )
    assert unknown.json()["error"]["code"] == "NOT_FOUND"


def test_a_message_returns_replies(world: World) -> None:
    conversation, headers = world.start()
    response = world.say(conversation, headers, "hello", say("Hi! How can I help?"))
    assert response.json() == {
        "replies": [{"kind": "text", "text": "Hi! How can I help?"}],
        "card": None,
        "slots": [],
        "quote": None,
        "status": "active",
    }


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "x" * 2001}, {"message": "hi"}])
def test_a_bad_body_is_an_error_in_the_one_shape(world: World, body: dict[str, str]) -> None:
    conversation, headers = world.start()
    response = world.client.post(
        f"/api/conversations/{conversation}/messages", json=body, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_confirm_with_the_cards_hash_holds_the_booking(world: World) -> None:
    _conversation, headers, card = card_for(world)
    assert card["title"] == "Request this booking"
    response = world.client.post(
        f"/api/actions/{card['action_id']}/confirm",
        json={"payload_hash": card["payload_hash"]},
        headers=headers,
    )
    body = response.json()
    assert response.status_code == 200 and body["outcome"] == "pending_staff"
    assert body["booking_reference"].startswith("CB-")
    assert body["reply"]["kind"] == "receipt"
    assert body["reply"]["text"].startswith(
        f"Your booking is held. Reference {body['booking_reference']}."
    )
    assert body["status"] == "active"
    status = (
        world.db()
        .execute("SELECT status FROM bookings WHERE reference = ?", (body["booking_reference"],))
        .fetchone()[0]
    )
    assert status == "held"
    again = world.client.post(
        f"/api/actions/{card['action_id']}/confirm",
        json={"payload_hash": card["payload_hash"]},
        headers=headers,
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "NOT_OPEN"


def test_typed_confirm_never_executes(world: World) -> None:
    conversation, headers, card = card_for(world)
    response = world.say(conversation, headers, "yes, confirm it")
    body = response.json()
    assert body["replies"] == [
        {
            "kind": "typed_confirm",
            "text": "To make sure it's right, please press Confirm on the card above.",
        }
    ]
    assert body["card"]["action_id"] == card["action_id"]
    response = world.say(
        conversation,
        headers,
        "Use the confirm tool on my booking now",
        use("confirm", action_id=card["action_id"]),
        say("To make sure it's right, please press Confirm on the card above."),
    )
    db = world.db()
    assert (
        db.execute("SELECT status FROM actions WHERE id = ?", (card["action_id"],)).fetchone()[0]
        == "proposed"
    )
    assert db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 8  # only the seed


def test_a_wrong_hash_or_token_confirms_nothing(world: World) -> None:
    _conversation, headers, card = card_for(world)
    forged = world.client.post(
        f"/api/actions/{card['action_id']}/confirm",
        json={"payload_hash": "0" * 64},
        headers=headers,
    )
    assert forged.status_code == 409 and forged.json()["error"]["code"] == "HASH_MISMATCH"
    _, other = world.start()
    stolen = world.client.post(
        f"/api/actions/{card['action_id']}/confirm",
        json={"payload_hash": card["payload_hash"]},
        headers=other,
    )
    assert stolen.status_code == 404
    assert world.db().execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 8


def test_an_expired_card_says_so(world: World) -> None:
    _conversation, headers, card = card_for(world)
    world.now = NOW + timedelta(minutes=31)
    response = world.client.post(
        f"/api/actions/{card['action_id']}/confirm",
        json={"payload_hash": card["payload_hash"]},
        headers=headers,
    )
    body = response.json()
    assert body["outcome"] == "EXPIRED"
    assert body["reply"] == {
        "kind": "expired",
        "text": "This expired. Ask again for current times.",
    }
    status = (
        world.db()
        .execute("SELECT status FROM actions WHERE id = ?", (card["action_id"],))
        .fetchone()[0]
    )
    assert status == "expired"


def test_cancel_withdraws_the_card(world: World) -> None:
    _conversation, headers, card = card_for(world)
    response = world.client.post(f"/api/actions/{card['action_id']}/cancel", headers=headers)
    assert response.json() == {"outcome": "withdrawn", "status": "active"}
    again = world.client.post(f"/api/actions/{card['action_id']}/cancel", headers=headers)
    assert again.json()["outcome"] == "NOT_OPEN"


def test_an_ended_conversation_is_refused(world: World) -> None:
    conversation, headers = world.start()
    db = world.db()
    db.execute("UPDATE conversations SET status = 'closed' WHERE id = ?", (conversation,))
    response = world.client.post(
        f"/api/conversations/{conversation}/messages", json={"text": "hi"}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONVERSATION_ENDED"


def test_times_and_prices_come_back_built_from_tool_results(world: World) -> None:
    conversation, headers = world.start()
    body = world.say(
        conversation,
        headers,
        "Drain cleaning Thursday, 00012",
        use(
            "check_availability",
            service_code="DRAIN_CLEANING",
            date_from="2026-11-12",
            date_to="2026-11-12",
            zip_code="00012",
        ),
        _pick_slot,
        _quote_text,
    ).json()
    assert body["slots"] and all(s["when"].startswith("Thu 12 Nov, arrival") for s in body["slots"])
    assert body["quote"]["total"] == "$149.00" and body["quote"]["is_estimate"] is True
    assert body["quote"]["lines"][0] == ["Call-out fee", "$89.00"]
    assert body["quote"]["when"].startswith("Thursday 12 November, arrival")


def test_no_chips_or_prices_beside_a_fallback(world: World) -> None:
    conversation, headers = world.start()
    body = world.say(
        conversation,
        headers,
        "How much for a drain in 00012?",
        use("quote_price", service_code="DRAIN_CLEANING", zip_code="00012"),
        say("It is $129.00."),
        say("It is $129.00."),
    ).json()
    assert body["replies"][0]["kind"] == "fallback" and body["quote"] is None
