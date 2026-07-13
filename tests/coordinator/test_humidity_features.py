"""Tests for humidity-aware cooling: feels-like targets + dew-point guard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.const import MODE_IDLE

from .conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)

# A cool-only TABS zone (Rehau-style summer modes) so cooling is reachable
_TABS_ROOM = {
    **SAMPLE_ROOM,
    "climate_mode": "cool_only",
    "comfort_cool": 24.0,
    "eco_cool": 26.5,
    "devices": [
        {
            "entity_id": "climate.living_room",
            "type": "trv",
            "role": "auto",
            "heating_system_type": "tabs",
            "idle_action": "off",
            "setpoint_mode": "proportional",
        }
    ],
    "heating_system_type": "tabs",
}

_ZONE_STATE = (
    "cool",
    {
        "hvac_modes": ["off", "cool"],
        "hvac_action": "idle",
        "min_temp": 16.0,
        "max_temp": 31.0,
        "temperature": 24.0,
        "current_temperature": 27.0,
    },
)


async def _run(hass, mock_config_entry, *, temp, humidity, settings=None, room=None):
    room = room or _TABS_ROOM
    store = _make_store_mock({room["area_id"]: room}, settings=settings)
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(
        side_effect=make_mock_states_get(
            temp=temp,
            humidity=humidity,
            outdoor_temp="28.0",
            extra={"climate.living_room": _ZONE_STATE},
        )
    )
    hass.services.async_call = AsyncMock()
    coordinator = _create_coordinator(hass, mock_config_entry)
    data = await coordinator._async_update_data()
    return data["rooms"][room["area_id"]]


class TestDewPointGuard:
    @pytest.mark.asyncio
    async def test_cooling_cut_when_air_near_dew_point(self, hass, mock_config_entry):
        """Room 27C at 90% RH -> dew point ~25.2C -> margin collapsed -> no cooling."""
        rs = await _run(hass, mock_config_entry, temp="27.0", humidity="90.0")
        assert rs["commanded_mode"] == MODE_IDLE
        assert rs["cooling_limited"] == "dew_point"
        assert rs["dew_point"] is not None and rs["dew_point"] > 24.0

    @pytest.mark.asyncio
    async def test_cooling_allowed_when_dry(self, hass, mock_config_entry):
        """Room 27C at 45% RH -> dew point ~14.4C -> cooling proceeds."""
        rs = await _run(hass, mock_config_entry, temp="27.0", humidity="45.0")
        assert rs["commanded_mode"] == "cooling"
        assert rs["cooling_limited"] == ""

    @pytest.mark.asyncio
    async def test_guard_disabled_by_setting(self, hass, mock_config_entry):
        rs = await _run(
            hass,
            mock_config_entry,
            temp="27.0",
            humidity="90.0",
            settings={"dewpoint_guard_enabled": False},
        )
        assert rs["commanded_mode"] == "cooling"

    @pytest.mark.asyncio
    async def test_guard_skipped_for_non_radiant(self, hass, mock_config_entry):
        """A radiator/AC room is not dew-point-limited (air units dehumidify)."""
        room = {
            **_TABS_ROOM,
            "heating_system_type": "",
            "devices": [{**_TABS_ROOM["devices"][0], "heating_system_type": ""}],
        }
        rs = await _run(hass, mock_config_entry, temp="27.0", humidity="90.0", room=room)
        assert rs["commanded_mode"] == "cooling"


class TestFeelsLike:
    @pytest.mark.asyncio
    async def test_humid_room_lowers_cool_target(self, hass, mock_config_entry):
        """70% RH -> bias = (70-50)/10*0.35 = 0.7 -> cool target 24.0 -> 23.3."""
        rs = await _run(
            hass,
            mock_config_entry,
            temp="24.1",  # would be idle vs 24.0, must cool vs 23.3
            humidity="70.0",
            settings={"feels_like_enabled": True, "dewpoint_guard_enabled": False},
        )
        assert rs["feels_like_delta"] == 0.7
        assert rs["cool_target"] == 23.3
        assert rs["commanded_mode"] == "cooling"

    @pytest.mark.asyncio
    async def test_disabled_by_default(self, hass, mock_config_entry):
        rs = await _run(hass, mock_config_entry, temp="24.1", humidity="70.0")
        assert rs["feels_like_delta"] == 0.0
        assert rs["cool_target"] == 24.0

    @pytest.mark.asyncio
    async def test_bias_clamped(self, hass, mock_config_entry):
        """99% RH would give 1.7 raw; clamp to 1.5."""
        rs = await _run(
            hass,
            mock_config_entry,
            temp="27.0",
            humidity="99.0",
            settings={"feels_like_enabled": True, "dewpoint_guard_enabled": False},
        )
        assert rs["feels_like_delta"] == 1.5
