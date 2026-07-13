"""Tests for the per-room decision trace ("why is this room heating?")."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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
