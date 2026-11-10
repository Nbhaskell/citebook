"""HTTP API (TRD section 11): customer routes here, staff routes in citebook/staff_api.py.

Every customer route needs the conversation's session token in the `X-Session-Token` header. The
token is random, returned once, and stored only as a SHA-256 hash. A wrong or missing token gets
the same 404 as an unknown id, so the API does not reveal which conversations exist.

Confirm is the only way a card is confirmed: it must carry the card's payload hash. Every error
has one shape: {"error": {"code", "message", "request_id"}}.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from citebook import __version__
from citebook.actions import Rules, confirm, withdraw
from citebook.api_common import ERRORS, ApiError, Clock, Db, Token
from citebook.config import Settings
from citebook.db import applied_versions, connect, transaction
from citebook.events import audit, log
from citebook.expiry import expire_due
from citebook.kb import FakeKB, KnowledgeBase, RagProjectKB
from citebook.loop import ConversationEnded, LoopConfig, _card, add_message, handle_message
from citebook.models import Ablation, ActionType, Actor
from citebook.outbox import booking_fields
from citebook.providers.base import ModelProvider
from citebook.providers.ollama import OllamaProvider
from citebook.staff_api import router as staff_router
from citebook.timeutil import to_utc_text
from citebook.tools.common import load_settings


class Status(BaseModel):
    """What the service can say about itself. `None` means the part could not be checked."""

    service: str
    version: str
    provider: str
    model: str
    synthetic_data: bool
    ablation: Ablation
    model_reachable: bool | None
    kb: str
    kb_reachable: bool | None
    database: str | None
    now: str
    clock: Literal["real", "demo", "fixed"]


class NewConversation(BaseModel):
    channel: Literal["web_widget", "simulator"] = "web_widget"


class ConversationCreated(BaseModel):
    conversation_id: str
    session_token: str
    status: str
    synthetic_data: bool


class CustomerMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ReplyOut(BaseModel):
    kind: str
    text: str


class CardOut(BaseModel):
    action_id: str
    action_type: str
    title: str
    rows: list[str]
    payload_hash: str
    expires_at: str | None


class SlotOut(BaseModel):
    slot_id: str
    when: str


class QuoteOut(BaseModel):
    lines: list[list[str]]
    total: str
    is_estimate: bool
    note: str
    when: str | None


class TurnOut(BaseModel):
    replies: list[ReplyOut]
    card: CardOut | None
    slots: list[SlotOut] = []
    quote: QuoteOut | None = None
    status: str


class ConfirmIn(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)


class ConfirmOut(BaseModel):
    outcome: str
    booking_reference: str | None
    reply: ReplyOut | None
    status: str


class TranscriptMessage(BaseModel):
    role: str
    text: str
    at: str


class TranscriptOut(BaseModel):
    status: str
    messages: list[TranscriptMessage]
    card: CardOut | None


class CancelOut(BaseModel):
    outcome: str
    status: str


EXPIRY_INTERVAL_S = 60


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def confirm_copy(
    conn: sqlite3.Connection, rules: Rules, action: sqlite3.Row, outcome: str
) -> str | None:
    """Fixed copy for the customer after Confirm (UI brief section 5). No model writes it."""
    kind = ActionType(action["action_type"])
    hours = rules.free_cancel_hours
    if outcome == "EXPIRED":
        return "This expired. Ask again for current times."
    if outcome == "failed":
        return "That time was just taken. Please choose another time and I'll check what's open."
    if outcome not in {"pending_staff", "executed"}:
        return None
    fields = booking_fields(conn, rules.clock, action["booking_id"]) if action["booking_id"] else {}
    if kind is ActionType.HOLD_CREATE:
        return (
            f"Your booking is held. Reference {fields['reference']}. Our team will confirm it, "
            f"usually within {rules.staff_confirm_typical_business_hours} business hours."
        )
    if kind is ActionType.RESCHEDULE:
        if outcome == "executed":
            return f"Your booking {fields['reference']} has moved to {fields['window']}."
        return (
            f"Because it's within {hours} hours, a team member will review the move and any fee. "
            "You'll hear back here."
        )
    if kind is ActionType.CANCELLATION:
        if outcome == "executed":
            return f"Your booking {fields['reference']} is cancelled."
        return (
            f"Because it's within {hours} hours, a team member will review the cancellation and "
            "any fee. You'll hear back here."
        )
    if kind is ActionType.REFUND_REQUEST:
        return "I've sent your refund request to our team. A person will review it and reply."
    return "I've sent your request to our team. A person will review it and reply."


def reachable(part: object) -> bool | None:
    """Ask a provider or knowledge base whether it answers; None if it cannot say."""
    check = getattr(part, "reachable", None)
    return bool(check()) if callable(check) else None


def database_state(settings: Settings) -> str:
    if not Path(settings.db_path).exists():
        return "missing: run python -m tools.seed_db"
    try:
        conn = connect(settings.db_path)
        try:
            version = applied_versions(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return "error"
    return f"ok (migration {version[-1]:03d})" if version else "empty: run python -m tools.seed_db"


async def expire_every_minute(settings: Settings, now_of: Clock) -> None:
    """Apply expiries every 60 seconds, so the staff queue and calendar stay current when
    nobody is chatting (TRD section 7.1)."""
    while True:
        await asyncio.sleep(EXPIRY_INTERVAL_S)
        try:
            await asyncio.to_thread(_expire_once, settings, now_of())
        except sqlite3.Error:
            log("expiry_failed")


def _expire_once(settings: Settings, now: datetime) -> None:
    if not Path(settings.db_path).exists():
        return
    conn = connect(settings.db_path)
    try:
        expire_due(conn, Rules.from_settings(load_settings(conn)), now)
    finally:
        conn.close()


def demo_clock(start: datetime, real: Clock | None = None) -> Clock:
    """A clock that reads `start` when made and then runs forward in real time (D-066)."""
    read_real: Clock = real or (lambda: datetime.now(UTC))
    began = read_real()
    return lambda: start.astimezone(UTC) + (read_real() - began)


def create_app(
    settings: Settings,
    *,
    provider: ModelProvider | None = None,
    kb: KnowledgeBase | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    clock_kind: Literal["real", "demo", "fixed"] = "fixed" if clock else "real"
    if clock is None and settings.demo_start is not None:
        clock = demo_clock(settings.demo_start)
        clock_kind = "demo"
    now_of: Clock = clock or (lambda: datetime.now(UTC))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(expire_every_minute(settings, now_of))
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="Citebook API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = now_of
    app.include_router(staff_router)
    parts: dict[str, object] = {}

    def get_provider() -> ModelProvider:
        if provider is not None:
            return provider
        if "provider" not in parts:
            parts["provider"] = OllamaProvider(
                settings.ollama_url,
                settings.model,
                think=settings.enable_thinking,
                timeout=settings.turn_timeout_s,
            )
        made: ModelProvider = parts["provider"]  # type: ignore[assignment]
        return made

    def get_kb() -> KnowledgeBase:
        if kb is not None:
            return kb
        if "kb" not in parts:
            parts["kb"] = (
                RagProjectKB(settings.kb_url)
                if settings.kb == "rag_project"
                else (FakeKB.from_docs())
            )
        made: KnowledgeBase = parts["kb"]  # type: ignore[assignment]
        return made

    def error(request: Request, status: int, code: str, message: str) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        body = {"error": {"code": code, "message": message, "request_id": request_id}}
        return JSONResponse(status_code=status, content=body)

    @app.middleware("http")
    async def request_ids(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = str(uuid.uuid4())
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(request, 422, "INVALID_REQUEST", "The request body or headers are not valid.")

    def conversation_for(
        conn: sqlite3.Connection, conversation_id: str, token: str | None
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        stored = row["session_token_hash"] if row else None
        presented = token_hash(token or "")
        if row is None or stored is None or not hmac.compare_digest(stored, presented):
            raise ApiError(404, "NOT_FOUND", "No such conversation.")
        found: sqlite3.Row = row
        return found

    def action_for(conn: sqlite3.Connection, action_id: str, token: str | None) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "No such card.")
        conversation_for(conn, row["conversation_id"], token)
        found: sqlite3.Row = row
        return found

    def status_of(conn: sqlite3.Connection, conversation_id: str) -> str:
        value: str = conn.execute(
            "SELECT status FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0]
        return value

    @app.get("/api/status")
    def status() -> Status:
        return Status(
            service="citebook",
            version=__version__,
            provider=settings.model_provider,
            model=settings.model,
            synthetic_data=settings.synthetic_data,
            ablation=settings.ablation,
            kb=settings.kb,
            model_reachable=reachable(get_provider()),
            kb_reachable=reachable(get_kb()),
            database=database_state(settings),
            now=to_utc_text(now_of()),
            clock=clock_kind,
        )

    @app.get("/api/conversations/{conversation_id}", responses=ERRORS)
    def transcript(conversation_id: str, conn: Db, x_session_token: Token = None) -> TranscriptOut:
        """The customer's view: their messages, the replies and any open card. No tool internals."""
        conversation = conversation_for(conn, conversation_id, x_session_token)
        rows = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ?"
            " AND role IN ('customer', 'assistant', 'staff') ORDER BY seq",
            (conversation_id,),
        ).fetchall()
        open_card = conn.execute(
            "SELECT id FROM actions WHERE conversation_id = ? AND status = 'proposed'",
            (conversation_id,),
        ).fetchone()
        card = _card(conn, open_card["id"]) if open_card else None
        return TranscriptOut(
            status=conversation["status"],
            messages=[
                TranscriptMessage(role=r["role"], text=r["content"], at=r["created_at"])
                for r in rows
            ],
            card=CardOut(**card.__dict__) if card else None,
        )

    @app.post("/api/conversations", status_code=201, responses=ERRORS)
    def start(conn: Db, body: NewConversation | None = None) -> ConversationCreated:
        token = secrets.token_urlsafe(32)
        conversation_id = str(uuid.uuid4())
        stamp = to_utc_text(now_of())
        with transaction(conn):
            conn.execute(
                "INSERT INTO conversations (id, channel, status, verification_state,"
                " started_at, last_activity_at, session_token_hash)"
                " VALUES (?, ?, 'active', 'anonymous', ?, ?, ?)",
                (
                    conversation_id,
                    (body or NewConversation()).channel,
                    stamp,
                    stamp,
                    token_hash(token),
                ),
            )
            audit(conn, Actor.CUSTOMER, "conversation_started", conversation_id=conversation_id)
        return ConversationCreated(
            conversation_id=conversation_id,
            session_token=token,
            status="active",
            synthetic_data=settings.synthetic_data,
        )

    @app.post("/api/conversations/{conversation_id}/messages", responses=ERRORS)
    def message(
        conversation_id: str, body: CustomerMessage, conn: Db, x_session_token: Token = None
    ) -> TurnOut:
        conversation_for(conn, conversation_id, x_session_token)
        config = LoopConfig(
            max_tool_calls=settings.max_tool_calls_per_turn,
            max_refusals=settings.max_consecutive_refusals,
            ablation=settings.ablation,
        )
        try:
            result = handle_message(
                conn, get_provider(), get_kb(), conversation_id, body.text, now_of(), config
            )
        except ConversationEnded as ended:
            raise ApiError(409, "CONVERSATION_ENDED", "This conversation has ended.") from ended
        return TurnOut(
            replies=[ReplyOut(kind=r.kind, text=r.text) for r in result.replies],
            card=CardOut(**result.card.__dict__) if result.card else None,
            slots=[SlotOut(slot_id=s.slot_id, when=s.when) for s in result.slots],
            quote=QuoteOut(**result.quote.__dict__) if result.quote else None,
            status=result.status,
        )

    @app.post("/api/actions/{action_id}/confirm", responses=ERRORS)
    def press_confirm(
        action_id: str, body: ConfirmIn, conn: Db, x_session_token: Token = None
    ) -> ConfirmOut:
        action = action_for(conn, action_id, x_session_token)
        now = now_of()
        rules = Rules.from_settings(load_settings(conn))
        expire_due(conn, rules, now)
        current = conn.execute("SELECT status FROM actions WHERE id = ?", (action_id,)).fetchone()
        if current["status"] == "expired":
            return ConfirmOut(
                outcome="EXPIRED",
                booking_reference=None,
                reply=ReplyOut(
                    kind="expired", text=str(confirm_copy(conn, rules, action, "EXPIRED"))
                ),
                status=status_of(conn, action["conversation_id"]),
            )
        result = confirm(conn, rules, action_id, action["conversation_id"], body.payload_hash, now)
        if result.outcome == "HASH_MISMATCH":
            raise ApiError(409, "HASH_MISMATCH", "This card changed. Ask again for a new card.")
        if result.outcome == "NOT_PROPOSED":
            raise ApiError(409, "NOT_OPEN", "This card is no longer open.")
        if result.outcome == "NOT_FOUND":
            raise ApiError(404, "NOT_FOUND", "No such card.")
        action = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        text = confirm_copy(conn, rules, action, result.outcome)
        reply = None
        if text:
            add_message(conn, action["conversation_id"], "assistant", text, now)
            kinds = {"failed": "failed", "EXPIRED": "expired"}
            reply = ReplyOut(kind=kinds.get(result.outcome, "receipt"), text=text)
        if result.outcome == "failed":
            add_message(
                conn,
                action["conversation_id"],
                "system_note",
                "The arrival window was taken before the customer confirmed. Offer to check "
                "availability again.",
                now,
            )
        return ConfirmOut(
            outcome=result.outcome,
            booking_reference=result.booking_reference,
            reply=reply,
            status=status_of(conn, action["conversation_id"]),
        )

    @app.post("/api/actions/{action_id}/cancel", responses=ERRORS)
    def press_cancel(action_id: str, conn: Db, x_session_token: Token = None) -> CancelOut:
        action = action_for(conn, action_id, x_session_token)
        done = withdraw(conn, action_id, action["conversation_id"], now_of())
        return CancelOut(
            outcome="withdrawn" if done else "NOT_OPEN",
            status=status_of(conn, action["conversation_id"]),
        )

    return app
