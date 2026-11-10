"""Seed files and knowledge-base documents are generated from the universe and kept in step."""

import re
from decimal import Decimal

from tools.build_data import KB_DOCS, SEED_DIR, build_all, load_facts

BUILT = build_all()
FACTS = load_facts()


def test_committed_files_match_the_generator() -> None:
    """Regenerate after changing the universe or the generator: python -m tools.build_data"""
    for path, text in BUILT.items():
        assert path.read_text(encoding="utf-8") == text, f"{path} is stale"
    committed = {*SEED_DIR.glob("*.json"), *KB_DOCS.glob("*.md")}
    assert committed == set(BUILT), "an old generated file is still committed"


def _json(name: str) -> object:
    import json

    return json.loads(BUILT[SEED_DIR / name])


def test_price_book_uses_minor_units_from_the_facts() -> None:
    book = _json("price_book.json")
    assert isinstance(book, dict)
    pricing = FACTS["pricing"]
    assert book["call_out_fee_minor"] == pricing["call_out_fee"]["price"] * 100
    assert book["emergency_call_out_fee_minor"] == pricing["emergency_call_out_fee"]["price"] * 100
    assert book["labour_per_hour_minor"] == pricing["labour"]["price_per_hour"] * 100
    assert book["outer_travel_fee_minor"] == FACTS["service_area"]["outer"]["travel_fee"] * 100
    assert (
        book["late_change_fee_minor"]
        == FACTS["policies"]["reschedule_and_cancel"]["late_fee"] * 100
    )
    assert Decimal(book["parts_sales_tax_rate"]) == Decimal("0.07")
    assert book["call_out_credited_when_work_approved"] is True
    assert book["version"].startswith("facts-")


def test_only_jobs_at_or_under_the_estimate_limit_are_bookable_online() -> None:
    services = _json("services.json")
    book = _json("price_book.json")
    assert isinstance(services, list) and isinstance(book, dict)
    limit = book["written_estimate_required_over_minor"]
    by_code = {s["code"]: s for s in services}
    for s in services:
        if s["pricing_kind"] == "quote_only":
            assert s["bookable_online"] == 0 and s["price_minor"] is None
        elif s["pricing_kind"] == "hourly":
            assert s["price_minor"] is None
            assert s["bookable_online"] == 1
        else:
            assert s["bookable_online"] == int(s["price_minor"] <= limit), s["code"]
    assert by_code["DRAIN_CLEANING"]["price_minor"] == 14900
    assert by_code["SEWER_LINE_CLEANING"]["bookable_online"] == 0  # $329 needs a written estimate
    assert by_code["WATER_HEATER_40_GAL"]["bookable_online"] == 0


def test_settings_hold_the_documented_keys() -> None:
    settings = _json("settings.json")
    assert isinstance(settings, dict)
    assert set(settings) == {
        "business_tz",
        "office_hours",
        "visit_hours",
        "closed_dates",
        "arrival_window_minutes",
        "hold_ttl_business_hours",
        "staff_confirm_typical_business_hours",
        "quote_ttl_minutes",
        "free_cancel_hours",
        "max_reschedules",
        "zip_areas",
        "verification_max_attempts",
        "conversation_idle_minutes",
        "synthetic_data",
    }
    assert settings["arrival_window_minutes"] == 120
    assert settings["free_cancel_hours"] == 24
    assert settings["synthetic_data"] is True


def _kb_text() -> str:
    return "\n".join(text for path, text in BUILT.items() if path.parent == KB_DOCS)


def _money(minor: int) -> str:
    dollars = minor // 100
    return f"${dollars:,}" if minor % 100 == 0 else f"${minor / 100:,.2f}"


def test_every_policy_number_appears_in_the_knowledge_base() -> None:
    """Rule: a number the bot may state must also be in the documents it retrieves from."""
    kb = _kb_text()
    book = _json("price_book.json")
    settings = _json("settings.json")
    services = _json("services.json")
    assert isinstance(book, dict) and isinstance(settings, dict) and isinstance(services, list)
    for key in [
        "call_out_fee_minor",
        "emergency_call_out_fee_minor",
        "labour_per_hour_minor",
        "outer_travel_fee_minor",
        "late_change_fee_minor",
        "written_estimate_required_over_minor",
    ]:
        assert _money(book[key]) in kb, key
    for s in services:
        if s["price_minor"] is not None:
            assert _money(s["price_minor"]) in kb, s["code"]
    assert f"{settings['free_cancel_hours']} hours" in kb
    assert f"{settings['hold_ttl_business_hours']} business hours" in kb
    assert f"{settings['staff_confirm_typical_business_hours']} business hours" in kb
    assert f"{settings['quote_ttl_minutes']} minutes" in kb
    assert re.search(rf"\b{settings['max_reschedules']} times\b", kb)
    assert f"{settings['verification_max_attempts']} attempts" in kb
    for code in settings["zip_areas"]:
        assert code in kb, code
