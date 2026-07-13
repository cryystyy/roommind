"""Tests for shadow mode (observe-only): decisions recorded, devices untouched."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)

_SHADOW_ROOM = {**SAMPLE_ROOM, "shadow_mode": True}

_DEVICE_STATE = (
    "heat",
    {
        "hvac_modes": ["off", "heat"],
        "hvac_action": "idle",
        "min_temp": 5.0,
        "max_temp": 30.0,
        "temperature": 20.0,
        "current_temperature": 18.0,
    },
)


async def _run(hass, mock_config_entry, room):
    store = _make_store_mock({room["area_id"]: room})
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(
        side_effect=make_mock_states_get(temp="18.0", extra={"climate.living_room": _DEVICE_STATE})
    )
    hass.services.async_call = AsyncMock()
    coordinator = _create_coordinator(hass, mock_config_entry)
    data = await coordinator._async_update_data()
    return coordinator, data["rooms"][room["area_id"]]


class TestShadowMode:
    @pytest.mark.asyncio
    async def test_no_commands_sent(self, hass, mock_config_entry):
        """Cold room would heat — shadow mode must not touch the device."""
        _c, rs = await _run(hass, mock_config_entry, _SHADOW_ROOM)
        climate_calls = [c for c in hass.services.async_call.call_args_list if c[0][0] == "climate"]
        assert climate_calls == [], f"shadow mode sent commands: {climate_calls}"
        # The would-be decision is still visible
        assert rs["commanded_mode"] == "heating"
        assert rs["decision_reason"] == "below_heat_target"

    @pytest.mark.asyncio
    async def test_decision_trace_flags_shadow(self, hass, mock_config_entry):
        coordinator, _rs = await _run(hass, mock_config_entry, _SHADOW_ROOM)
        d = coordinator._decision_traces[_SHADOW_ROOM["area_id"]][-1]
        assert d["shadow"] is True
        assert d["mode"] == "heating"

    @pytest.mark.asyncio
    async def test_display_follows_observed_device(self, hass, mock_config_entry):
        """Display shows what the device actually does (idle), not the intent."""
        _c, rs = await _run(hass, mock_config_entry, _SHADOW_ROOM)
        assert rs["mode"] == "idle"  # device hvac_action is idle

    @pytest.mark.asyncio
    async def test_normal_room_still_commands(self, hass, mock_config_entry):
        """Regression: without the flag the same scenario drives the device."""
        await _run(hass, mock_config_entry, SAMPLE_ROOM)
        climate_calls = [c for c in hass.services.async_call.call_args_list if c[0][0] == "climate"]
        assert climate_calls, "expected device commands without shadow mode"
