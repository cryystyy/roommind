"""Tests for Full Control display-mode reconciliation.

When RoomMind commands idle but the physical device is observably still
heating/cooling (high-latency slab systems, external control), the mode
sensor shows the observed action instead of "idle".  Internal tracking
stays on the commanded mode; `commanded_mode` is surfaced separately.
"""

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


async def _run_cycle(hass, mock_config_entry, hvac_action: str):
    """Run one update with room temp at the cool target (commanded idle)
    and the zone reporting *hvac_action*."""
    room = {
        **SAMPLE_ROOM,
        "devices": [
            {
                "entity_id": "climate.living_room",
                "type": "trv",
                "role": "auto",
                "heating_system_type": "",
                "idle_action": "off",
                "setpoint_mode": "proportional",
            }
        ],
    }
    states_get = make_mock_states_get(
        temp="24.0",  # == comfort_cool -> bang-bang stays idle
        extra={
            "climate.living_room": (
                "cool",
                {
                    "hvac_modes": ["off", "cool"],
                    "hvac_action": hvac_action,
                    "min_temp": 16.0,
                    "max_temp": 31.0,
                    "temperature": 24.0,
                    "current_temperature": 25.0,
                },
            ),
        },
    )
    store = _make_store_mock({room["area_id"]: room})
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(side_effect=states_get)
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    data = await coordinator._async_update_data()
    return data["rooms"][room["area_id"]]


class TestDisplayReconciliation:
    @pytest.mark.asyncio
    async def test_commanded_idle_but_device_cooling_shows_cooling(self, hass, mock_config_entry):
        room_state = await _run_cycle(hass, mock_config_entry, hvac_action="cooling")
        assert room_state["commanded_mode"] == MODE_IDLE
        assert room_state["mode"] == "cooling"

    @pytest.mark.asyncio
    async def test_commanded_idle_and_device_idle_shows_idle(self, hass, mock_config_entry):
        room_state = await _run_cycle(hass, mock_config_entry, hvac_action="idle")
        assert room_state["commanded_mode"] == MODE_IDLE
        assert room_state["mode"] == MODE_IDLE
