"""Tests for the ResidualHeatTracker manager."""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from custom_components.roommind.const import MODE_COOLING, MODE_HEATING, MODE_IDLE
from custom_components.roommind.managers.residual_heat_tracker import (
    _COOL_STATE_MAX_AGE_MINUTES,
    ResidualHeatTracker,
)

# ---------------------------------------------------------------------------
# update – cleanup branch (lines 40-43)
# ---------------------------------------------------------------------------


def test_update_clears_state_when_residual_zero_and_idle():
    """When mode is idle, previous was idle, and q_residual==0, state is cleaned up."""
    tracker = ResidualHeatTracker()
    # Seed some state as if heating stopped earlier
    tracker._off_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 900.0

    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=0.0)

    assert "room1" not in tracker._off_since
    assert "room1" not in tracker._off_power
    assert "room1" not in tracker._on_since


def test_update_keeps_state_when_residual_nonzero():
    """When q_residual > 0, state should NOT be cleaned up."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 900.0

    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=0.5)

    assert "room1" in tracker._off_since
    assert "room1" in tracker._off_power
    assert "room1" in tracker._on_since


def test_update_cleanup_no_state_is_noop():
    """Cleanup branch with no existing state should not raise."""
    tracker = ResidualHeatTracker()
    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=0.0)
    assert "room1" not in tracker._off_since


# ---------------------------------------------------------------------------
# clear_room (line 53)
# ---------------------------------------------------------------------------


def test_clear_room_delegates_to_remove():
    """clear_room should remove all state for the given room."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 900.0

    tracker.clear_room("room1")

    assert "room1" not in tracker._off_since
    assert "room1" not in tracker._off_power
    assert "room1" not in tracker._on_since


# ---------------------------------------------------------------------------
# clear_all (lines 57-59)
# ---------------------------------------------------------------------------


def test_clear_all_removes_all_rooms():
    """clear_all should remove state for every room."""
    tracker = ResidualHeatTracker()
    for room in ("room1", "room2", "room3"):
        tracker._off_since[room] = 1000.0
        tracker._off_power[room] = 0.8
        tracker._on_since[room] = 900.0

    tracker.clear_all()

    assert len(tracker._off_since) == 0
    assert len(tracker._off_power) == 0
    assert len(tracker._on_since) == 0


# ---------------------------------------------------------------------------
# get_q_residual – core logic
# ---------------------------------------------------------------------------


def test_get_q_residual_no_state_returns_zero():
    """Room not tracked at all returns 0.0."""
    tracker = ResidualHeatTracker()
    result = tracker.get_q_residual("unknown_room", "radiator", MODE_IDLE)
    assert result == 0.0


def test_get_q_residual_no_off_since_returns_zero():
    """Room tracked (on_since set) but no off_since returns 0.0."""
    tracker = ResidualHeatTracker()
    tracker._on_since["room1"] = 1000.0
    result = tracker.get_q_residual("room1", "radiator", MODE_IDLE)
    assert result == 0.0


def test_get_q_residual_previous_mode_heating_returns_zero():
    """When previous_mode is HEATING, residual heat is always 0."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._on_since["room1"] = 900.0
    tracker._off_power["room1"] = 0.8
    result = tracker.get_q_residual("room1", "radiator", MODE_HEATING)
    assert result == 0.0


def test_get_q_residual_empty_system_type_returns_zero():
    """Empty system_type returns 0.0 (no residual heat without known system)."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._on_since["room1"] = 900.0
    result = tracker.get_q_residual("room1", "", MODE_IDLE)
    assert result == 0.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_computes_correctly_radiator(mock_time):
    """Verify computed residual heat matches compute_residual_heat for radiator."""
    now = 2000.0
    on_time = 1700.0  # started heating at t=1700
    off_time = 1900.0  # stopped heating at t=1900
    mock_time.time.return_value = now

    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = off_time
    tracker._on_since["room1"] = on_time
    tracker._off_power["room1"] = 0.7

    result = tracker.get_q_residual("room1", "radiator", MODE_IDLE)

    # Hardcoded expected value from the formula:
    #   elapsed = (2000-1900)/60 = 1.6667 min, heat_dur = (1900-1700)/60 = 3.3333 min
    #   radiator: tau=10, initial=0.3, tau_charge=15
    #   charge = 1 - exp(-3.3333/15) = 0.19927
    #   q = 0.3 * 0.19927 * exp(-1.6667/10) * 0.7 = ~0.03542
    import math

    charge = 1 - math.exp(-3.3333 / 15.0)
    expected = 0.3 * charge * math.exp(-1.6667 / 10.0) * 0.7
    assert result == pytest.approx(expected, abs=1e-6)
    assert result == pytest.approx(0.03542, abs=1e-3)


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_computes_correctly_underfloor(mock_time):
    """Verify computed residual heat matches compute_residual_heat for underfloor."""
    now = 5000.0
    on_time = 1000.0  # long heating run
    off_time = 4500.0
    mock_time.time.return_value = now

    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = off_time
    tracker._on_since["room1"] = on_time
    tracker._off_power["room1"] = 1.0

    result = tracker.get_q_residual("room1", "underfloor", MODE_IDLE)

    # Hardcoded expected value from the formula:
    #   elapsed = (5000-4500)/60 = 8.3333 min, heat_dur = (4500-1000)/60 = 58.3333 min
    #   underfloor: tau=90, initial=0.85, tau_charge=60
    #   charge = 1 - exp(-58.3333/60) = 0.62136
    #   q = 0.85 * 0.62136 * exp(-8.3333/90) * 1.0 = ~0.48137
    charge = 1 - math.exp(-58.3333 / 60.0)
    expected = 0.85 * charge * math.exp(-8.3333 / 90.0) * 1.0
    assert result == pytest.approx(expected, abs=1e-6)
    assert result == pytest.approx(0.4814, abs=1e-2)


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_no_on_since_uses_off_since_as_fallback(mock_time):
    """When _on_since is missing, heat_dur should be 0 (off_since - off_since)."""
    now = 2000.0
    off_time = 1900.0
    mock_time.time.return_value = now

    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = off_time
    # No _on_since set — fallback to off_since in .get()

    result = tracker.get_q_residual("room1", "radiator", MODE_IDLE)

    # heat_dur = 0 → charge_fraction = 1.0 (fully charged assumption)
    # elapsed = (2000-1900)/60 = 1.6667 min, pf defaults to 1.0
    # q = 0.3 * 1.0 * exp(-1.6667/10) * 1.0 = ~0.2539
    expected_val = 0.3 * math.exp(-1.6667 / 10.0)
    assert result == pytest.approx(expected_val, abs=1e-3)


# ---------------------------------------------------------------------------
# update – heating transitions
# ---------------------------------------------------------------------------


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_heating_mode_records_on_since(mock_time):
    """Starting heating (from non-heating) records _on_since."""
    mock_time.time.return_value = 5000.0
    tracker = ResidualHeatTracker()

    tracker.update("room1", MODE_HEATING, 0.6, MODE_IDLE)

    assert tracker._on_since["room1"] == 5000.0
    # off_since should be cleared (was never set, so just not present)
    assert "room1" not in tracker._off_since
    # power fraction recorded
    assert tracker._off_power["room1"] == 0.6


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_heating_continued_does_not_reset_on_since(mock_time):
    """Continued heating (previous was also HEATING) does not overwrite _on_since."""
    tracker = ResidualHeatTracker()
    tracker._on_since["room1"] = 3000.0  # original start time

    mock_time.time.return_value = 4000.0
    tracker.update("room1", MODE_HEATING, 0.8, MODE_HEATING)

    # on_since should remain the original value
    assert tracker._on_since["room1"] == 3000.0
    # power fraction updated
    assert tracker._off_power["room1"] == 0.8


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_heating_to_idle_transition(mock_time):
    """Transitioning from HEATING to IDLE records _off_since."""
    tracker = ResidualHeatTracker()
    # First: start heating
    mock_time.time.return_value = 1000.0
    tracker.update("room1", MODE_HEATING, 0.9, MODE_IDLE)
    assert tracker._on_since["room1"] == 1000.0

    # Then: stop heating
    mock_time.time.return_value = 2000.0
    tracker.update("room1", MODE_IDLE, 0.0, MODE_HEATING)

    assert tracker._off_since["room1"] == 2000.0
    # on_since preserved for duration calculation
    assert tracker._on_since["room1"] == 1000.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_idle_to_heating_clears_off_since(mock_time):
    """Re-starting heating after idle clears _off_since and sets new _on_since."""
    tracker = ResidualHeatTracker()
    # Establish off_since (as if heating stopped previously)
    tracker._off_since["room1"] = 1500.0
    tracker._on_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8

    # Now start heating again
    mock_time.time.return_value = 2000.0
    tracker.update("room1", MODE_HEATING, 0.7, MODE_IDLE)

    assert "room1" not in tracker._off_since
    assert tracker._on_since["room1"] == 2000.0
    assert tracker._off_power["room1"] == 0.7


# ---------------------------------------------------------------------------
# remove_room – all dicts cleared
# ---------------------------------------------------------------------------


def test_remove_room_clears_all_dicts():
    """remove_room clears all internal dicts for the given room."""
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 1000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 900.0
    # Other room should be unaffected
    tracker._off_since["room2"] = 2000.0
    tracker._off_power["room2"] = 0.5
    tracker._on_since["room2"] = 1800.0

    tracker.remove_room("room1")

    assert "room1" not in tracker._off_since
    assert "room1" not in tracker._off_power
    assert "room1" not in tracker._on_since
    # room2 untouched
    assert "room2" in tracker._off_since
    assert "room2" in tracker._off_power
    assert "room2" in tracker._on_since


# ---------------------------------------------------------------------------
# Cooling transitions ("cold charge")
# ---------------------------------------------------------------------------


def _seed_cooling(tracker, room, off_since, on_since, power):
    tracker._cool_off_since[room] = off_since
    tracker._cool_on_since[room] = on_since
    tracker._cool_off_power[room] = power


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_cooling_start_records_cool_on_since(mock_time):
    """Starting cooling (from non-cooling) records _cool_on_since, heating dicts untouched."""
    mock_time.time.return_value = 5000.0
    tracker = ResidualHeatTracker()

    tracker.update("room1", MODE_COOLING, 0.6, MODE_IDLE)

    assert tracker._cool_on_since["room1"] == 5000.0
    assert "room1" not in tracker._cool_off_since
    assert tracker._cool_off_power["room1"] == 0.6
    # No cross-talk into heating state
    assert "room1" not in tracker._on_since
    assert "room1" not in tracker._off_since
    assert "room1" not in tracker._off_power


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_cooling_continued_does_not_reset_cool_on_since(mock_time):
    """Continued cooling (previous was also COOLING) does not overwrite _cool_on_since."""
    tracker = ResidualHeatTracker()
    tracker._cool_on_since["room1"] = 3000.0

    mock_time.time.return_value = 4000.0
    tracker.update("room1", MODE_COOLING, 0.8, MODE_COOLING)

    assert tracker._cool_on_since["room1"] == 3000.0
    assert tracker._cool_off_power["room1"] == 0.8


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_cooling_to_idle_transition(mock_time):
    """Transitioning from COOLING to IDLE records _cool_off_since, keeps _cool_on_since."""
    tracker = ResidualHeatTracker()
    mock_time.time.return_value = 1000.0
    tracker.update("room1", MODE_COOLING, 0.9, MODE_IDLE)

    mock_time.time.return_value = 2000.0
    tracker.update("room1", MODE_IDLE, 0.0, MODE_COOLING)

    assert tracker._cool_off_since["room1"] == 2000.0
    assert tracker._cool_on_since["room1"] == 1000.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_idle_does_not_clear_fresh_cooling_state(mock_time):
    """The heating q_residual==0.0 cleanup must NOT wipe fresh cooling state."""
    tracker = ResidualHeatTracker()
    _seed_cooling(tracker, "room1", off_since=100_000.0, on_since=90_000.0, power=1.0)
    # Stale heating state should still be cleaned by the existing branch
    tracker._off_since["room1"] = 99_000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 98_000.0

    mock_time.time.return_value = 105_000.0
    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=0.0)

    # Heating state cleared (unchanged behavior)
    assert "room1" not in tracker._off_since
    # Fresh cooling state kept
    assert tracker._cool_off_since["room1"] == 100_000.0
    assert tracker._cool_on_since["room1"] == 90_000.0
    assert tracker._cool_off_power["room1"] == 1.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_idle_clears_stale_cooling_state(mock_time):
    """Cooling state older than 5 tau (slowest profile) is cleaned up."""
    tracker = ResidualHeatTracker()
    _seed_cooling(tracker, "room1", off_since=100_000.0, on_since=90_000.0, power=1.0)

    mock_time.time.return_value = 100_000.0 + _COOL_STATE_MAX_AGE_MINUTES * 60.0 + 60.0
    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=0.0)

    assert "room1" not in tracker._cool_off_since
    assert "room1" not in tracker._cool_on_since
    assert "room1" not in tracker._cool_off_power


# ---------------------------------------------------------------------------
# get_charge_fraction – cold charge
# ---------------------------------------------------------------------------


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_cold_charge_rises_while_cooling(mock_time):
    """While cooling the charge saturates toward 1.0 with tau_charge."""
    t0 = 100_000.0
    tracker = ResidualHeatTracker()
    mock_time.time.return_value = t0
    tracker.update("room1", MODE_COOLING, 1.0, MODE_IDLE)

    mock_time.time.return_value = t0 + 3600  # 60 min in
    mid = tracker.get_charge_fraction("room1", "tabs", MODE_COOLING)
    mock_time.time.return_value = t0 + 4 * 3600  # 240 min in
    full = tracker.get_charge_fraction("room1", "tabs", MODE_COOLING)

    # tabs: tau_charge=180 → 1 - exp(-minutes/180), pf=1.0
    assert mid == pytest.approx(1.0 - math.exp(-60.0 / 180.0), abs=1e-6)
    assert full == pytest.approx(1.0 - math.exp(-240.0 / 180.0), abs=1e-6)
    assert 0.0 < mid < full < 1.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_cold_charge_decays_after_cooling_stops(mock_time):
    """After cooling stops the stored cold charge decays with tau."""
    t0 = 100_000.0
    tracker = ResidualHeatTracker()
    mock_time.time.return_value = t0
    tracker.update("room1", MODE_COOLING, 1.0, MODE_IDLE)
    mock_time.time.return_value = t0 + 4 * 3600  # cooled for 240 min
    tracker.update("room1", MODE_IDLE, 0.0, MODE_COOLING)

    stored = 1.0 - math.exp(-240.0 / 180.0)  # charge at the off-transition
    mock_time.time.return_value = t0 + 5 * 3600  # 60 min after stop
    decayed1 = tracker.get_charge_fraction("room1", "tabs", MODE_IDLE)
    mock_time.time.return_value = t0 + 9 * 3600  # 300 min after stop
    decayed2 = tracker.get_charge_fraction("room1", "tabs", MODE_IDLE)

    assert decayed1 == pytest.approx(stored * math.exp(-60.0 / 240.0), abs=1e-6)
    assert decayed2 == pytest.approx(stored * math.exp(-300.0 / 240.0), abs=1e-6)
    assert stored > decayed1 > decayed2 > 0.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_active_cooling_wins_over_old_heating_state(mock_time):
    """Heat→cool flip: while cooling the ACTIVE direction's charging curve is shown."""
    t0 = 100_000.0
    tracker = ResidualHeatTracker()
    mock_time.time.return_value = t0
    tracker.update("room1", MODE_HEATING, 1.0, MODE_IDLE)
    mock_time.time.return_value = t0 + 7200  # heated 120 min, then flip to cooling
    tracker.update("room1", MODE_COOLING, 0.6, MODE_HEATING)

    mock_time.time.return_value = t0 + 7200 + 3600  # 60 min of cooling
    charge = tracker.get_charge_fraction("room1", "tabs", MODE_COOLING)
    assert charge == pytest.approx((1.0 - math.exp(-60.0 / 180.0)) * 0.6, abs=1e-6)

    # Heating residual tracking is unaffected by the cooling run
    assert tracker._off_since["room1"] == t0 + 7200
    assert tracker.get_q_residual("room1", "tabs", MODE_COOLING) > 0.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_most_recent_transition_wins_when_both_exist(mock_time):
    """Season change: with both residuals stored, the newer off-transition wins."""
    now = 200_000.0
    mock_time.time.return_value = now
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 190_000.0
    tracker._on_since["room1"] = 180_000.0
    tracker._off_power["room1"] = 0.8
    _seed_cooling(tracker, "room1", off_since=195_000.0, on_since=185_000.0, power=0.4)

    # Cooling stopped more recently → cooling charge
    run_dur = (195_000.0 - 185_000.0) / 60.0
    elapsed = (now - 195_000.0) / 60.0
    expected_cool = (1.0 - math.exp(-run_dur / 180.0)) * 0.4 * math.exp(-elapsed / 240.0)
    assert tracker.get_charge_fraction("room1", "tabs", MODE_IDLE) == pytest.approx(expected_cool, abs=1e-6)

    # Flip recency: heating stopped more recently → heating charge
    tracker._off_since["room1"] = 196_000.0
    run_dur_h = (196_000.0 - 180_000.0) / 60.0
    elapsed_h = (now - 196_000.0) / 60.0
    expected_heat = (1.0 - math.exp(-run_dur_h / 180.0)) * 0.8 * math.exp(-elapsed_h / 240.0)
    assert tracker.get_charge_fraction("room1", "tabs", MODE_IDLE) == pytest.approx(expected_heat, abs=1e-6)


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_heating_charge_math_unchanged(mock_time):
    """Regression: heating charge values are byte-identical to the pre-refactor formula."""
    mock_time.time.return_value = 5000.0
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 4500.0
    tracker._on_since["room1"] = 1000.0
    tracker._off_power["room1"] = 1.0

    result = tracker.get_charge_fraction("room1", "underfloor", MODE_IDLE)

    heat_dur = (4500.0 - 1000.0) / 60.0
    elapsed = (5000.0 - 4500.0) / 60.0
    expected = (1.0 - math.exp(-heat_dur / 60.0)) * math.exp(-elapsed / 90.0)
    assert result == pytest.approx(expected, abs=1e-12)

    # Charging curve while heating, also unchanged
    tracker2 = ResidualHeatTracker()
    tracker2._on_since["room1"] = 1000.0
    tracker2._off_power["room1"] = 0.5
    mock_time.time.return_value = 1000.0 + 90 * 60.0
    result2 = tracker2.get_charge_fraction("room1", "underfloor", MODE_HEATING)
    assert result2 == pytest.approx((1.0 - math.exp(-90.0 / 60.0)) * 0.5, abs=1e-12)


def test_remove_room_and_clear_all_clear_cooling_dicts():
    """Cooling state is cleaned up alongside heating state."""
    tracker = ResidualHeatTracker()
    _seed_cooling(tracker, "room1", off_since=1000.0, on_since=900.0, power=0.8)
    _seed_cooling(tracker, "room2", off_since=2000.0, on_since=1800.0, power=0.5)

    tracker.remove_room("room1")
    assert "room1" not in tracker._cool_off_since
    assert "room1" not in tracker._cool_on_since
    assert "room1" not in tracker._cool_off_power
    assert "room2" in tracker._cool_off_since

    tracker.clear_all()
    assert len(tracker._cool_off_since) == 0
    assert len(tracker._cool_on_since) == 0
    assert len(tracker._cool_off_power) == 0


# ---------------------------------------------------------------------------
# get_q_residual – signed cold residual (cooling coast-down)
# ---------------------------------------------------------------------------


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_cooling_returns_negative_with_correct_magnitude(mock_time):
    """A finished cooling run yields a NEGATIVE residual with heating-formula magnitude."""
    from custom_components.roommind.control.residual_heat import compute_residual_heat

    now = 200_000.0
    mock_time.time.return_value = now
    tracker = ResidualHeatTracker()
    # Cooled for 4 h, stopped 10 min ago, at 80 % power
    _seed_cooling(tracker, "room1", off_since=now - 600.0, on_since=now - 600.0 - 4 * 3600.0, power=0.8)

    result = tracker.get_q_residual("room1", "tabs", MODE_IDLE)

    expected_mag = compute_residual_heat(10.0, "tabs", 0.8, 240.0)
    assert expected_mag > 0.0
    assert result == pytest.approx(-expected_mag, abs=1e-9)


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_cooling_decays_toward_zero(mock_time):
    """The cold residual magnitude decays as time since the off-transition grows."""
    now = 200_000.0
    tracker = ResidualHeatTracker()
    _seed_cooling(tracker, "room1", off_since=now, on_since=now - 4 * 3600.0, power=1.0)

    mock_time.time.return_value = now + 600.0  # 10 min after stop
    early = tracker.get_q_residual("room1", "tabs", MODE_IDLE)
    mock_time.time.return_value = now + 4 * 3600.0  # 4 h after stop
    late = tracker.get_q_residual("room1", "tabs", MODE_IDLE)

    assert early < late < 0.0  # both negative, magnitude shrinking


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_cooling_suppressed_while_cooling(mock_time):
    """While previous_mode is COOLING the cold residual reads 0 (mirror of heating)."""
    now = 200_000.0
    mock_time.time.return_value = now
    tracker = ResidualHeatTracker()
    _seed_cooling(tracker, "room1", off_since=now - 600.0, on_since=now - 4000.0, power=1.0)

    assert tracker.get_q_residual("room1", "tabs", MODE_COOLING) == 0.0
    assert tracker.get_q_residual("room1", "tabs", MODE_IDLE) < 0.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_most_recent_off_transition_wins(mock_time):
    """Season change: with both residual states, the newer off-transition's sign wins."""
    now = 200_000.0
    mock_time.time.return_value = now
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = now - 3600.0  # heating stopped 60 min ago
    tracker._on_since["room1"] = now - 3600.0 - 7200.0
    tracker._off_power["room1"] = 1.0
    _seed_cooling(tracker, "room1", off_since=now - 600.0, on_since=now - 600.0 - 7200.0, power=1.0)

    # Cooling stopped more recently → negative
    assert tracker.get_q_residual("room1", "tabs", MODE_IDLE) < 0.0

    # Flip recency: heating stopped more recently → positive
    tracker._off_since["room1"] = now - 300.0
    assert tracker.get_q_residual("room1", "tabs", MODE_IDLE) > 0.0


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_get_q_residual_heating_regression_byte_identical(mock_time):
    """Heating-only state: value identical to the pre-signed formula (incl. stale cool state)."""
    now = 5000.0
    mock_time.time.return_value = now
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 4500.0
    tracker._on_since["room1"] = 1000.0
    tracker._off_power["room1"] = 1.0

    charge = 1 - math.exp(-(3500.0 / 60.0) / 60.0)
    expected = 0.85 * charge * math.exp(-(500.0 / 60.0) / 90.0) * 1.0
    assert tracker.get_q_residual("room1", "underfloor", MODE_IDLE) == pytest.approx(expected, abs=1e-9)

    # An OLDER cooling off-transition must not change the heating result
    _seed_cooling(tracker, "room1", off_since=4000.0, on_since=2000.0, power=1.0)
    assert tracker.get_q_residual("room1", "underfloor", MODE_IDLE) == pytest.approx(expected, abs=1e-9)

    # Heating residual still visible while actively cooling (pre-existing behavior)
    assert tracker.get_q_residual("room1", "underfloor", MODE_COOLING) == pytest.approx(expected, abs=1e-9)


@patch("custom_components.roommind.managers.residual_heat_tracker.time")
def test_update_negative_residual_keeps_heating_state(mock_time):
    """A negative (cold) residual is not 'heating decayed to 0' — heating state stays."""
    mock_time.time.return_value = 10_000.0
    tracker = ResidualHeatTracker()
    tracker._off_since["room1"] = 9000.0
    tracker._off_power["room1"] = 0.8
    tracker._on_since["room1"] = 8000.0
    _seed_cooling(tracker, "room1", off_since=9500.0, on_since=9200.0, power=1.0)

    tracker.update("room1", MODE_IDLE, 0.0, MODE_IDLE, q_residual=-0.4)

    assert "room1" in tracker._off_since
    assert "room1" in tracker._cool_off_since
