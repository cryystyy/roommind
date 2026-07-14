"""Tests for the per-room decision trace ("why is this room heating?")."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.const import TargetTemps

from .conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)


async def _run(hass, mock_config_entry, *, temp="18.0", room=None, settings=None, window_sensors=None):
    room = room or SAMPLE_ROOM
    store = _make_store_mock({room["area_id"]: room}, settings=settings)
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(side_effect=make_mock_states_get(temp=temp, window_sensors=window_sensors or {}))
    hass.services.async_call = AsyncMock()
    coordinator = _create_coordinator(hass, mock_config_entry)
    data = await coordinator._async_update_data()
    return coordinator, data["rooms"][room["area_id"]]


class TestDecisionTrace:
    @pytest.mark.asyncio
    async def test_heating_reason_and_schedule_source(self, hass, mock_config_entry):
        """Cold room in a comfort schedule window: heating below heat target."""
        coordinator, rs = await _run(hass, mock_config_entry, temp="18.0")
        assert rs["decision_reason"] == "below_heat_target"
        assert rs["decision_target_source"] == "schedule"
        trace = coordinator._decision_traces[SAMPLE_ROOM["area_id"]]
        assert len(trace) == 1
        d = trace[-1]
        assert d["mode"] == "heating"
        assert d["heat_target"] == 21.0
        assert d["can_heat"] is True

    @pytest.mark.asyncio
    async def test_window_open_reason(self, hass, mock_config_entry):
        room = {**SAMPLE_ROOM, "window_sensors": ["binary_sensor.window1"]}
        _c, rs = await _run(
            hass,
            mock_config_entry,
            temp="18.0",
            room=room,
            window_sensors={"binary_sensor.window1": "on"},
        )
        assert rs["decision_reason"] == "window_open"
        assert rs["commanded_mode"] == "idle"

    @pytest.mark.asyncio
    async def test_deadband_reason(self, hass, mock_config_entry):
        """Room between heat (21) and cool (24) targets: idle in comfort band."""
        _c, rs = await _run(hass, mock_config_entry, temp="22.0")
        assert rs["decision_reason"] == "in_deadband"

    @pytest.mark.asyncio
    async def test_climate_disabled_reason(self, hass, mock_config_entry):
        room = {**SAMPLE_ROOM, "climate_control_enabled": False}
        _c, rs = await _run(hass, mock_config_entry, temp="18.0", room=room)
        assert rs["decision_reason"] == "climate_disabled"

    @pytest.mark.asyncio
    async def test_trace_dedupes_identical_cycles(self, hass, mock_config_entry):
        """Consecutive identical decisions don't flood the ring buffer."""
        room = SAMPLE_ROOM
        store = _make_store_mock({room["area_id"]: room})
        hass.data = {"roommind": {"store": store}}
        hass.states.get = MagicMock(side_effect=make_mock_states_get(temp="18.0"))
        hass.services.async_call = AsyncMock()
        coordinator = _create_coordinator(hass, mock_config_entry)
        for _ in range(5):
            await coordinator._async_update_data()
        assert len(coordinator._decision_traces[room["area_id"]]) == 1


def _record(hass, mock_config_entry, **overrides):
    """Call _record_decision directly with sane cooling-season defaults."""
    hass.states.get = MagicMock(side_effect=make_mock_states_get())
    coordinator = _create_coordinator(hass, mock_config_entry)
    kwargs = dict(
        area_id=SAMPLE_ROOM["area_id"],
        room=SAMPLE_ROOM,
        settings={},
        targets=TargetTemps(heat=23.0, cool=26.5),
        mode="cooling",
        power_fraction=0.4,
        current_temp=25.3,
        can_heat=True,
        can_cool=True,
        mpc_active=True,
        window_open=False,
        force_off=False,
        presence_away=False,
        climate_active=True,
        waiting_for_data=False,
        cooling_limited="",
        feels_like_delta=0.0,
    )
    kwargs.update(overrides)
    return coordinator._record_decision(**kwargs)


class TestPrecoolPreheatReasons:
    """MPC pre-conditioning must not be mislabeled as above/below target."""

    def test_mpc_precooling_at_or_below_cool_target(self, hass, mock_config_entry):
        """MPC cooling a room already below the cool target = pre-cooling."""
        d = _record(hass, mock_config_entry)  # 25.3 <= 26.5, MPC active
        assert d["reason"] == "mpc_precooling"
        # Boundary: exactly at target still counts as pre-cooling
        d = _record(hass, mock_config_entry, current_temp=26.5)
        assert d["reason"] == "mpc_precooling"

    def test_above_cool_target_when_actually_above(self, hass, mock_config_entry):
        d = _record(hass, mock_config_entry, current_temp=27.2)
        assert d["reason"] == "above_cool_target"

    def test_bangbang_cooling_keeps_legacy_reason(self, hass, mock_config_entry):
        """Without MPC (bang-bang / min-run hold) the legacy reason is kept."""
        d = _record(hass, mock_config_entry, mpc_active=False)
        assert d["reason"] == "above_cool_target"

    def test_unknown_temp_keeps_legacy_reason(self, hass, mock_config_entry):
        d = _record(hass, mock_config_entry, current_temp=None)
        assert d["reason"] == "above_cool_target"

    def test_mpc_preheating_at_or_above_heat_target(self, hass, mock_config_entry):
        d = _record(hass, mock_config_entry, mode="heating", current_temp=23.4)
        assert d["reason"] == "mpc_preheating"

    def test_below_heat_target_when_actually_below(self, hass, mock_config_entry):
        d = _record(hass, mock_config_entry, mode="heating", current_temp=21.0)
        assert d["reason"] == "below_heat_target"

    def test_dominant_constraints_win_over_precooling(self, hass, mock_config_entry):
        """Dew-point limit (and the other dominant constraints) outrank pre-cooling."""
        d = _record(hass, mock_config_entry, cooling_limited="dew_point")
        assert d["reason"] == "dew_point_limited"


class TestCoastingReason:
    """Idle outside the active band while the MPC plans = deliberate coasting."""

    def test_coasting_above_cool_target(self, hass, mock_config_entry):
        """Idle at 27.2 > cool 26.5 with an active MPC plan → coasting on stored cold."""
        d = _record(hass, mock_config_entry, mode="idle", power_fraction=0.0, current_temp=27.2)
        assert d["reason"] == "mpc_coasting"

    def test_coasting_below_heat_target(self, hass, mock_config_entry):
        """Mirror: idle at 22.0 < heat 23.0 → coasting on stored heat."""
        d = _record(hass, mock_config_entry, mode="idle", power_fraction=0.0, current_temp=22.0)
        assert d["reason"] == "mpc_coasting"

    def test_idle_inside_band_stays_plan_idle(self, hass, mock_config_entry):
        """In the deadband there is no drift being tolerated — plain mpc_plan_idle."""
        d = _record(hass, mock_config_entry, mode="idle", power_fraction=0.0, current_temp=25.3)
        assert d["reason"] == "mpc_plan_idle"

    def test_no_coasting_without_mpc(self, hass, mock_config_entry):
        """Bang-bang idle above target (hysteresis) is not MPC coasting."""
        d = _record(
            hass, mock_config_entry, mode="idle", power_fraction=0.0, current_temp=27.2, mpc_active=False
        )
        assert d["reason"] == "in_deadband"

    def test_no_coasting_when_direction_incapable(self, hass, mock_config_entry):
        """Above the cool target without cooling capability is not coasting."""
        d = _record(
            hass,
            mock_config_entry,
            mode="idle",
            power_fraction=0.0,
            current_temp=27.2,
            can_cool=False,
            targets=TargetTemps(heat=23.0, cool=None),
        )
        assert d["reason"] == "mpc_plan_idle"

    def test_unknown_temp_stays_plan_idle(self, hass, mock_config_entry):
        d = _record(hass, mock_config_entry, mode="idle", power_fraction=0.0, current_temp=None)
        assert d["reason"] == "mpc_plan_idle"

    def test_dominant_constraints_win_over_coasting(self, hass, mock_config_entry):
        """window_open / dew-point etc. still outrank the coasting label."""
        d = _record(
            hass, mock_config_entry, mode="idle", power_fraction=0.0, current_temp=27.2, window_open=True
        )
        assert d["reason"] == "window_open"
        d = _record(
            hass,
            mock_config_entry,
            mode="idle",
            power_fraction=0.0,
            current_temp=27.2,
            cooling_limited="dew_point",
        )
        assert d["reason"] == "dew_point_limited"

    def test_active_cooling_unaffected(self, hass, mock_config_entry):
        """The v1.8.1 precooling/above-target chain for ACTIVE modes is untouched."""
        d = _record(hass, mock_config_entry, current_temp=27.2)  # mode="cooling" default
        assert d["reason"] == "above_cool_target"
        d = _record(hass, mock_config_entry, current_temp=25.3)
        assert d["reason"] == "mpc_precooling"
