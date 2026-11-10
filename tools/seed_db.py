"""Load the seed into a database: settings, services, technicians, customers, the calendar and
the seeded bookings with their quotes.

Run from the repository root:  python -m tools.seed_db [--fresh]
--fresh deletes the database file at CITEBOOK_DB_PATH first. Tests call `load_seed` directly on
an in-memory database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from typing import Any

from citebook.db import connect, migrate, transaction
from citebook.pricing import build_quote
from citebook.timeutil import BusinessClock, parse_utc
from tools.build_data import SEED_DIR, stable_uuid
from tools.seed_calendar import generate_calendar

# Fixed so the seed is the same on every run; all seeded rows predate both seed states.
SEEDED_AT = "2026-11-02T17:00:00Z"


def _seed(name: str) -> Any:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


def load_seed(conn: sqlite3.Connection, calendar_seed: int | None = None) -> None:
    settings = _seed("settings.json")
    book = _seed("price_book.json")
    services = _seed("services.json")
    clock = BusinessClock.from_settings(settings)
    calendar = generate_calendar(calendar_seed)
    with transaction(conn):
        conn.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            [(k, json.dumps(v)) for k, v in sorted(settings.items())],
        )
        conn.executemany(
            "INSERT INTO services (code, name, description, skill, windows_needed, pricing_kind,"
            " price_minor, min_hours, bookable_online, first_step_code, active)"
            " VALUES (:code, :name, :description, :skill, :windows_needed, :pricing_kind,"
            " :price_minor, :min_hours, :bookable_online, NULL, :active)",
            services,
        )
        conn.executemany(  # first steps after every row exists, for the self-reference
            "UPDATE services SET first_step_code = :first_step_code WHERE code = :code",
            [s for s in services if s["first_step_code"]],
        )
        conn.executemany(
            "INSERT INTO technicians (id, name, skills, active) VALUES (?, ?, ?, ?)",
            [
                (t["id"], t["name"], json.dumps(t["skills"]), t["active"])
                for t in _seed("technicians.json")
            ],
        )
        customers = {c["id"]: c for c in _seed("customers.json")}
        conn.executemany(
            "INSERT INTO customers (id, name, phone, email, address, zip_code, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (c["id"], c["name"], c["phone"], c["email"], c["address"], c["zip_code"], SEEDED_AT)
                for c in customers.values()
            ],
        )
        conn.executemany(
            "INSERT INTO slots (id, technician_id, starts_at, ends_at, status, version)"
            " VALUES (:id, :technician_id, :starts_at, :ends_at, :status, :version)",
            calendar["slots"],
        )
        slots = {s["id"]: s for s in calendar["slots"]}
        by_code = {s["code"]: s for s in services}
        for booking in calendar["bookings"]:
            customer = customers[booking["customer_id"]]
            start = parse_utc(slots[booking["slot_id"]]["starts_at"])
            quote = build_quote(
                by_code[booking["service_code"]],
                customer["zip_code"],
                start,
                book,
                settings["zip_areas"],
                clock,
            )
            quote_id = stable_uuid("quote", booking["reference"])
            conn.execute(
                "INSERT INTO quotes (id, conversation_id, service_code, slot_id, zip_code, lines,"
                " total_minor, is_estimate, estimate_note, price_book_version, created_at,"
                " expires_at) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    quote_id,
                    quote.service_code,
                    booking["slot_id"],
                    quote.zip_code,
                    json.dumps([{"label": label, "amount_minor": a} for label, a in quote.lines]),
                    quote.total_minor,
                    int(quote.is_estimate),
                    quote.estimate_note or None,
                    quote.price_book_version,
                    SEEDED_AT,
                    SEEDED_AT,
                ),
            )
            conn.execute(
                "INSERT INTO bookings (id, reference, customer_id, service_code, slot_id, quote_id,"
                " status, reschedule_count, hold_expires_at, customer_notes, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    booking["id"],
                    booking["reference"],
                    booking["customer_id"],
                    booking["service_code"],
                    booking["slot_id"],
                    quote_id,
                    booking["status"],
                    booking["reschedule_count"],
                    booking["customer_notes"],
                    SEEDED_AT,
                    SEEDED_AT,
                ),
            )


def main(argv: Sequence[str] | None = None) -> int:
    from citebook.config import Settings

    parser = argparse.ArgumentParser(description="Create and seed the Citebook database.")
    parser.add_argument("--fresh", action="store_true", help="delete the database file first")
    args = parser.parse_args(argv)
    path = Settings().db_path
    if args.fresh and path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        if conn.execute("SELECT name FROM sqlite_master WHERE name = 'slots'").fetchone():
            count = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
            if count:
                print(f"{path} already holds a seed; use --fresh to rebuild it", file=sys.stderr)
                return 1
        migrate(conn)
        load_seed(conn)
        slots = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
        bookings = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    finally:
        conn.close()
    print(f"Seeded {path}: {slots} slots, {bookings} bookings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
