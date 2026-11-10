"""Ported from Citedesk tests/test_safety.py (D-052)."""

import pytest

from citebook.screening import emergency_hits


def has_emergency_terms(text: str) -> bool:
    return bool(emergency_hits(text))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("i think i smell gas in the basement", "gas_smell"),
        ("There is a strong gas smell near the stove", "gas_smell"),
        ("faint rotten egg smell near our stove", "gas_smell"),
        ("we have a gas leak", "gas_smell"),
        ("our carbon monoxide detector went off", "carbon_monoxide"),
        ("the CO alarm is beeping", "carbon_monoxide"),
        ("BASEMENT IS FLOODING FAST", "flooding"),
        ("the kitchen flooded overnight", "flooding"),
        ("water everywhere in the hallway", "flooding"),
        ("a pipe burst in the kitchen wall", "burst_pipe"),
        ("we have a burst pipe", "burst_pipe"),
        ("sewage is coming up through the drain", "sewage"),
        ("the sewer is backing up into the house", "sewage"),
    ],
)
def test_emergency_terms_are_found(text: str, expected: str) -> None:
    assert expected in emergency_hits(text)
    assert has_emergency_terms(text)


@pytest.mark.parametrize(
    "text",
    [
        "how much does drain cleaning cost",
        "our current water heater is gas, can you replace it with another gas one",
        "we have a gas stove and a gas dryer",
        "we are redoing the flooring in the kitchen",
        "a burst of cold air came from the vent",
        "I need the carbon filter changed",
        "the water heater is making a loud banging noise",
        "my sump pump runs a lot",
        "please ignore my earlier message about the date",
        "",
    ],
)
def test_ordinary_messages_have_no_emergency_terms(text: str) -> None:
    assert emergency_hits(text) == []
    assert not has_emergency_terms(text)


def test_matching_ignores_case_and_odd_spacing() -> None:
    assert has_emergency_terms("I   SMELL    GAS!!")
    assert has_emergency_terms("Burst\nPipe")


def test_zero_width_characters_do_not_hide_a_term() -> None:
    assert has_emergency_terms("sm​ell ga​s")


def test_hits_are_names_not_text() -> None:
    hits = emergency_hits("sewage coming up and I smell gas")
    assert set(hits) == {"gas_smell", "sewage"}
    assert all(h.isidentifier() for h in hits)


# --- sewage described without the word (D-032 named one golden item that does this) ----------


@pytest.mark.parametrize(
    "text",
    [
        "when the washer drains the toilet upstairs bubbles and now theres dirty smelly water "
        "with toilet paper in it on the laundry room floor",
        "brown smelly water is all over the basement floor",
        "there is toilet paper floating in the shower",
        "toilet paper and water coming out of the floor drain",
        "water keeps bubbling up out of the floor drain",
        "dirty water coming up through the tub",
        "foul water came up from the shower drain this morning",
        "THE TOILET IS OVERFLOWING AND DIRTY WATER IS ON THE FLOOR",
    ],
)
def test_sewage_is_found_when_the_message_describes_it_instead_of_naming_it(text: str) -> None:
    assert "sewage" in emergency_hits(text)


@pytest.mark.parametrize(
    "text",
    [
        "my kitchen sink drains slowly and gurgles",
        "I want a toilet paper holder installed in the bathroom",
        "the toilet paper dispenser in the cloakroom is cracked",
        "I spilled dirty mop water in the garage",
        "the water from the tap is brown for a minute after it has been off",
        "the floor drain in the garage is slow",
        "bubbles in the toilet when the washer drains",
        "we want to replace the shower drain cover",
    ],
)
def test_ordinary_drain_talk_is_not_sewage(text: str) -> None:
    assert emergency_hits(text) == []


# --- mentions of something long past -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "A pipe burst in our kitchen last month and my insurance company wants documentation.",
        "our basement flooded two years ago and we want a quote for a sump pump",
        "back in 2019 the sewer backed up, can you inspect the line",
        "we had flooding last winter so I would like the pipes checked",
        "the last time a pipe burst was months ago and the repair held up fine",
    ],
)
def test_a_clearly_historical_mention_is_not_an_emergency(text: str) -> None:
    assert emergency_hits(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A pipe burst here last year. Right now the basement is flooding!", {"flooding"}),
        ("the pipe burst last night and water is everywhere", {"burst_pipe", "flooding"}),
        ("we flooded last week and the floor is still soaked", {"flooding"}),
        ("the pipe that burst last month has burst again", {"burst_pipe"}),
        ("had flooding last year please help water everywhere", {"flooding"}),
        ("our basement flooded last year and is flooding now", {"flooding"}),
        ("a pipe burst last month, today the sewer is backing up into the tub", {"sewage"}),
    ],
)
def test_a_historical_mention_never_hides_a_present_one(text: str, expected: set[str]) -> None:
    assert expected <= set(emergency_hits(text))


def test_history_discounts_only_the_clause_it_is_in() -> None:
    hits = emergency_hits("The pipe burst last month. I smell gas now.")
    assert hits == ["gas_smell"]


# --- an event that is only imagined (D-085) ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "If a pipe bursts at night, do you send someone out, and what do you charge?",
        "Do you come out after hours in case of a burst pipe?",
        "What if the basement floods, is there an emergency number?",
        "If the toilet overflows on a Sunday do you work weekends?",
        "What are your call-out hours? I want to know who to ring if a pipe bursts.",
    ],
)
def test_an_imagined_water_event_is_not_an_emergency(text: str) -> None:
    assert emergency_hits(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "A pipe burst in the kitchen wall",
        "If you can't come soon the basement will flood",
        "A pipe burst and if you can't come the flood will get worse",
        "What if it floods again? The basement flooded this morning",
        "If a pipe bursts again we are in trouble. The pipe burst this morning.",
        "water everywhere, what if the pipe bursts",
    ],
)
def test_a_real_water_event_still_fires_next_to_an_imagined_one(text: str) -> None:
    assert emergency_hits(text) != []


@pytest.mark.parametrize(
    "text",
    [
        "What should I do if I smell gas?",
        "If the carbon monoxide alarm goes off what do I do",
    ],
)
def test_gas_and_carbon_monoxide_terms_always_fire(text: str) -> None:
    assert emergency_hits(text) != []
