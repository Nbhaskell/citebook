"""SQLite connection and the migration runner.

    python -m citebook.db migrate     apply pending migrations from db/migrations/

Migrations are numbered, append-only SQL files (001_init.sql, 002_....sql), never edited after
merge. Each runs in its own transaction together with the row that records it, so a failure
leaves nothing half applied.
"""

import re
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
MIGRATION_NAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
BUSY_TIMEOUT_SECONDS = 5.0  # how long a write waits for another connection's lock


class MigrationError(Exception):
    """A problem with the migration files or with a migration while it was applied."""


def utc_now() -> str:
    """The current time as an ISO 8601 UTC string, the format used in every timestamp column."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a database with foreign keys on, rows as mappings, and WAL for file databases.

    The connection is in autocommit mode: use `transaction()` to group statements. A write that
    finds the database locked waits `BUSY_TIMEOUT_SECONDS` and then fails with
    `sqlite3.OperationalError`; nothing is half written.
    """
    conn = sqlite3.connect(
        str(path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit if the block finishes, roll back if it raises. Joins an open transaction.

    `BEGIN IMMEDIATE` takes the write lock at the start, so two writers never interleave.
    """
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def discover(directory: Path) -> dict[int, tuple[str, str]]:
    """Version to (name, sql) for every migration file, checked for naming and numbering."""
    found: dict[int, tuple[str, str]] = {}
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME.match(path.name)
        if not match:
            raise MigrationError(
                f"bad migration file name {path.name!r}: expected NNN_lower_case_name.sql"
            )
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"duplicate migration number {version:03d}")
        found[version] = (match.group(2), path.read_text("utf-8"))
    for expected, version in enumerate(sorted(found), start=1):
        if version != expected:
            raise MigrationError(f"gap in migration numbers: {expected:03d} is missing")
    return found


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if not exists:
        return []
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return [int(r["version"]) for r in rows]


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations in order and return the versions applied."""
    migrations = discover(directory)
    applied = applied_versions(conn)
    unknown = [v for v in applied if v not in migrations]
    if unknown:
        raise MigrationError(
            f"the database has migration {unknown[0]:03d}, which this code does not know: "
            "the database is newer than the code"
        )
    done: list[int] = []
    for version in sorted(migrations):
        if version in applied:
            continue
        name, sql = migrations[version]
        try:
            # The script runs inside an open transaction that is committed only after the
            # migration is recorded, so a failure at any point rolls everything back.
            conn.executescript(f"BEGIN;\n{sql}")
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, utc_now()),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"migration {version:03d}_{name} failed: {error}") from error
        done.append(version)
    return done


def main(argv: list[str] | None = None) -> int:
    from citebook.config import Settings

    args = sys.argv[1:] if argv is None else argv
    if args != ["migrate"]:
        print("usage: python -m citebook.db migrate", file=sys.stderr)
        return 2
    path = Settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        applied = migrate(conn)
        current = applied_versions(conn)
    except MigrationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    if applied:
        print(f"Applied migration(s): {', '.join(f'{v:03d}' for v in applied)}")
    else:
        print(f"Database is up to date (migration {current[-1]:03d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
