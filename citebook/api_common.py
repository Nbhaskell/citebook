"""Shared HTTP pieces for the customer and staff routes: database dependency, errors, clock."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, Request
from pydantic import BaseModel

from citebook.db import connect

Clock = Callable[[], datetime]


def database(request: Request) -> Iterator[sqlite3.Connection]:
    """One connection per request (SQLite in WAL mode)."""
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def now(request: Request) -> datetime:
    """The app's clock: real time, or a fixed time that tests and the scenario runner set."""
    clock: Clock = request.app.state.clock
    return clock()


Db = Annotated[sqlite3.Connection, Depends(database)]
Now = Annotated[datetime, Depends(now)]
Token = Annotated[str | None, Header(alias="X-Session-Token")]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
