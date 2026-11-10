import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from citebook.db import (
    MIGRATIONS_DIR,
    MigrationError,
    applied_versions,
    connect,
    migrate,
    transaction,
    utc_now,
)

SCHEMA_DOC = Path(__file__).resolve().parent.parent / "docs" / "BACKEND_SCHEMA.md"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = connect(":memory:")
    migrate(c)
    yield c
    c.close()


def test_connect_turns_foreign_keys_on_and_returns_rows(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_file_databases_use_wal(tmp_path: Path) -> None:
    c = connect(tmp_path / "x.db")
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    c.close()


def test_utc_now_is_iso_8601_utc() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", utc_now())


def test_migration_holds_exactly_the_tables_in_the_schema_document() -> None:
    doc = SCHEMA_DOC.read_text(encoding="utf-8")
    block = re.search(r"## 3\. Tables\n\n```sql\n(.*?)```", doc, re.S)
    assert block, "schema section 3 not found"
    migration = (MIGRATIONS_DIR / "001_init.sql").read_text(encoding="utf-8")
    assert block.group(1) in migration


def test_all_migrations_apply_and_are_recorded(conn: sqlite3.Connection) -> None:
    assert applied_versions(conn) == [1]
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"conversations", "bookings", "slots", "actions", "outbox", "audit_events"} <= names


def test_migrating_twice_applies_nothing(conn: sqlite3.Connection) -> None:
    assert migrate(conn) == []


def test_check_constraints_reject_unknown_values(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO conversations (id, channel, status, verification_state, started_at,"
            " last_activity_at) VALUES ('c', 'email', 'active', 'anonymous', 'x', 'x')"
        )


def test_foreign_keys_are_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO slots (id, technician_id, starts_at, ends_at, status)"
            " VALUES ('s', 'no-such-tech', 'a', 'b', 'open')"
        )


def test_tool_actions_need_a_tool_call_and_others_must_not_have_one(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "INSERT INTO conversations (id, channel, status, verification_state, started_at,"
        " last_activity_at) VALUES ('c', 'web_widget', 'active', 'anonymous', 'x', 'x')"
    )
    insert = (
        "INSERT INTO actions (id, conversation_id, origin, tool_call_id, action_type, status,"
        " payload, summary, payload_hash, created_at, updated_at)"
        " VALUES (?, 'c', ?, NULL, 'handoff', 'pending_staff', '{}', 's', 'h', 'x', 'x')"
    )
    conn.execute(insert, ("a1", "screening"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("a2", "tool"))


def test_quote_only_services_cannot_be_bookable_online(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO services (code, name, description, skill, windows_needed, pricing_kind,"
            " price_minor, bookable_online) VALUES ('X', 'x', 'x', 'other', 1, 'quote_only',"
            " NULL, 1)"
        )


def test_transaction_rolls_back_on_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO settings (key, value) VALUES ('k', '1')")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_a_failing_migration_is_rolled_back(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "001_ok.sql",
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL, applied_at TEXT NOT NULL); CREATE TABLE a (x);",
    )
    _write(tmp_path, "002_bad.sql", "CREATE TABLE b (y); THIS IS NOT SQL;")
    c = connect(":memory:")
    with pytest.raises(MigrationError, match="002_bad"):
        migrate(c, tmp_path)
    assert applied_versions(c) == [1]
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "b" not in tables


@pytest.mark.parametrize("name", ["1_init.sql", "001-init.sql", "001_Init.sql"])
def test_badly_named_files_are_refused(tmp_path: Path, name: str) -> None:
    _write(tmp_path, name, "SELECT 1;")
    with pytest.raises(MigrationError, match="bad migration file name"):
        migrate(connect(":memory:"), tmp_path)


def test_gaps_in_the_numbering_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    _write(tmp_path, "003_c.sql", "SELECT 1;")
    with pytest.raises(MigrationError, match="002 is missing"):
        migrate(connect(":memory:"), tmp_path)


def test_a_database_newer_than_the_code_is_refused(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    conn.execute("INSERT INTO schema_migrations VALUES (2, 'future', 'x')")
    with pytest.raises(MigrationError, match="newer than the code"):
        migrate(conn)
