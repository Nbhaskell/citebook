"""The smoke-test script is importable and has ten conversations (it needs a model to run)."""

from tools.smoke_test import CONFIRM, SCRIPTS


def test_ten_named_conversations() -> None:
    assert len(SCRIPTS) == 10
    assert len({s.name for s in SCRIPTS}) == 10
    assert sum(CONFIRM in s.turns for s in SCRIPTS) >= 4
    assert all(s.expect for s in SCRIPTS)
