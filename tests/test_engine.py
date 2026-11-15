"""engine.py: starting and manual changes."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.abstract_state_machine import engine, model

T0 = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
MACHINE = model.parse("Dishwasher", model.new_machine_data(["cleared", "loaded", "running", "clean"], "cleared", None))


def test_initial_without_restore_is_fallback() -> None:
    assert engine.initial(MACHINE, None, T1) == engine.Status("cleared", None, engine.REASON_INITIAL, T1)


def test_initial_restores_known_state() -> None:
    restored = engine.Status("running", "loaded", "manual", T0, True)
    assert engine.initial(MACHINE, restored, T1) == engine.Status("running", "loaded", engine.REASON_RESTORED, T0, True)


def test_initial_with_removed_state_falls_back() -> None:
    restored = engine.Status("drying", "running", "manual", T0)
    assert engine.initial(MACHINE, restored, T1).state == "cleared"


def test_initial_drops_removed_previous() -> None:
    restored = engine.Status("clean", "drying", "manual", T0)
    assert engine.initial(MACHINE, restored, T1).previous is None


def test_set_state_moves_and_reports() -> None:
    status = engine.initial(MACHINE, None, T0)
    new, transition = engine.set_state(MACHINE, status, "loaded", T1, reason="NFC tag") or (None, None)
    assert new == engine.Status("loaded", "cleared", "NFC tag", T1, True)
    assert transition == engine.Transition("cleared", "loaded", "NFC tag", True, T1)


def test_set_state_to_current_is_a_no_op() -> None:
    status = engine.initial(MACHINE, None, T0)
    assert engine.set_state(MACHINE, status, "cleared", T1) is None


def test_set_state_unknown() -> None:
    with pytest.raises(engine.UnknownState):
        engine.set_state(MACHINE, engine.initial(MACHINE, None, T0), "drying", T1)
