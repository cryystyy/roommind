"""Regression tests for cooling on thermostat-typed (trv) devices.

Covers the Rehau-NEA-Smart / TABS scenario: zones configured as
Type="Thermostat" that report hvac_modes ["off", "cool"] in cooling season.

Bug 2: the proportional path never entered COOLING because can_cool was
granted only by AC-typed devices, and MODE_COOLING turned thermostats off.

Bug 1: idle actions resolved the idle setpoint to min_temp unconditionally,
which on a cooling device is MAXIMUM cooling demand (overcool).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roommind.const import (
    MODE_COOLING,
    MODE_HEATING,
    MODE_IDLE,
    TargetTemps,
)
from custom_components.roommind.control.mpc_controller import (
    MPCController,
    async_idle_device,
    async_turn_off_climate,
    check_trvs_can_cool,
    check_trvs_can_heat,
    clear_command_cache,
    get_can_heat_cool,
)
from custom_components.roommind.control.thermal_model import RoomModelManager
from custom_components.roommind.utils.device_utils import (
    state_is_cool_only,
    state_supports_heating,
)

from .conftest import build_hass, make_room


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_command_cache()
    yield
    clear_command_cache()


def _state(state: str, attrs: dict | None = None):
    s = MagicMock()
    s.state = state
    s.attributes = attrs or {}
    return s


def _cool_only_state(state="off", min_temp=16.0, max_temp=31.0, temperature=24.0):
    """A Rehau-style zone: summer modes [off, cool]."""
    return _state(
        state,
        {
            "hvac_modes": ["off", "cool"],
            "min_temp": min_temp,
            "max_temp": max_temp,
            "temperature": temperature,
        },
    )


def _heat_only_state(state="off", min_temp=5.0, max_temp=30.0, temperature=20.0):
    return _state(
        state,
        {
            "hvac_modes": ["off", "heat"],
            "min_temp": min_temp,
            "max_temp": max_temp,
            "temperature": temperature,
        },
    )


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


class TestCapabilityDetection:
    def test_state_is_cool_only(self):
        assert state_is_cool_only(_cool_only_state())
        assert not state_is_cool_only(_heat_only_state())
        assert not state_is_cool_only(None)
        # Heat-capable devices keep legacy thermostat semantics
        assert not state_is_cool_only(_state("off", {"hvac_modes": ["off", "heat_cool"]}))
        assert not state_is_cool_only(_state("off", {"hvac_modes": ["off", "heat", "cool"]}))
        # Unreliable modes never grant cooling
        assert not state_is_cool_only(_state("off", {"hvac_modes": ["off", "fan_only"]}))

    def test_state_supports_heating(self):
        assert state_supports_heating(_heat_only_state())
        # Unavailable / unreliable modes assume heating (legacy behavior)
        assert state_supports_heating(None)
        assert state_supports_heating(_state("off", {"hvac_modes": ["off"]}))
        # Reliably cool-only denies heating
        assert not state_supports_heating(_cool_only_state())

    def test_check_trvs_can_cool_and_heat(self):
        hass = build_hass()
        room = make_room(thermostats=["climate.tabs"])
        hass.states.get = MagicMock(return_value=_cool_only_state())
        assert check_trvs_can_cool(hass, room)
        assert not check_trvs_can_heat(hass, room)

        hass.states.get = MagicMock(return_value=_heat_only_state())
        assert not check_trvs_can_cool(hass, room)
        assert check_trvs_can_heat(hass, room)

    def test_get_can_heat_cool_trv_cooling(self):
        room = make_room(thermostats=["climate.tabs"])
        # Legacy behavior: no ACs -> no cooling
        can_heat, can_cool = get_can_heat_cool(room, 27.0)
        assert can_heat is False  # outdoor 27 > heating_max 22
        assert can_cool is False
        # Cool-capable TRV grants cooling; reliably cool-only denies heating
        can_heat, can_cool = get_can_heat_cool(room, 27.0, trvs_can_cool=True, trvs_can_heat=False)
        assert can_cool is True
        assert can_heat is False


# ---------------------------------------------------------------------------
# Bug 2: cooling decision + apply on a cool-only trv-typed device
# ---------------------------------------------------------------------------


def _make_controller(hass, room, **kwargs):
    defaults = dict(
        model_manager=RoomModelManager(),
        outdoor_temp=27.0,
        settings={},
        has_external_sensor=True,
    )
    defaults.update(kwargs)
    return MPCController(hass, room, **defaults)


class TestCoolingDecision:
    @pytest.mark.asyncio
    async def test_bangbang_enters_cooling_for_cool_only_trv(self):
        """Room 27C, cool target 24C, Type=Thermostat cool-only zone -> COOLING."""
        hass = build_hass()
        hass.states.get = MagicMock(return_value=_cool_only_state())
        room = make_room(thermostats=["climate.tabs_office"])
        ctrl = _make_controller(hass, room)
        mode, pf = await ctrl.async_evaluate(27.0, TargetTemps(heat=23.0, cool=24.0))
        assert mode == MODE_COOLING
        assert pf == 1.0

    @pytest.mark.asyncio
    async def test_bangbang_stops_cooling_at_target(self):
        """At/below the cool target the room returns to idle (no overcool)."""
        hass = build_hass()
        hass.states.get = MagicMock(return_value=_cool_only_state(state="cool"))
        room = make_room(thermostats=["climate.tabs_office"])
        ctrl = _make_controller(hass, room, previous_mode=MODE_COOLING)
        mode, _pf = await ctrl.async_evaluate(23.9, TargetTemps(heat=23.0, cool=24.0))
        assert mode == MODE_IDLE

    @pytest.mark.asyncio
    async def test_cool_only_trv_does_not_heat(self):
        """Below heat target, a reliably cool-only zone must NOT be heated."""
        hass = build_hass()
        hass.states.get = MagicMock(return_value=_cool_only_state())
        room = make_room(thermostats=["climate.tabs_office"])
        ctrl = _make_controller(hass, room, outdoor_temp=18.0)  # heating not outdoor-gated
        mode, _pf = await ctrl.async_evaluate(20.0, TargetTemps(heat=23.0, cool=26.0))
        assert mode == MODE_IDLE

    @pytest.mark.asyncio
    async def test_heat_only_trv_still_heats(self):
        """Regression: normal heat-only TRV keeps heating below target."""
        hass = build_hass()
        hass.states.get = MagicMock(return_value=_heat_only_state())
        room = make_room()
        ctrl = _make_controller(hass, room, outdoor_temp=5.0)
        mode, _pf = await ctrl.async_evaluate(18.0, TargetTemps(heat=21.0, cool=None))
        assert mode == MODE_HEATING


class TestCoolingApply:
    @pytest.mark.asyncio
    async def test_apply_cooling_drives_cool_capable_trv(self):
        """MODE_COOLING sends set_hvac_mode(cool) + boost setpoint to the TRV."""
        hass = build_hass()
        hass.states.get = MagicMock(return_value=_cool_only_state())
        room = make_room(thermostats=["climate.tabs_office"])
        ctrl = _make_controller(hass, room)
        await ctrl.async_apply(MODE_COOLING, TargetTemps(heat=23.0, cool=24.0), 1.0, current_temp=27.0)

        calls = hass.services.async_call.call_args_list
        mode_calls = [c for c in calls if c[0][1] == "set_hvac_mode"]
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]
        assert any(
            c[0][2]["entity_id"] == "climate.tabs_office" and c[0][2]["hvac_mode"] == "cool" for c in mode_calls
        ), f"no set_hvac_mode(cool) on the TRV; calls: {calls}"
        # No off command may reach the cool-capable TRV
        assert not any(c[0][2].get("hvac_mode") == "off" for c in mode_calls)
        # Boost setpoint at/below target, at/above device min (16)
        trv_temps = [c[0][2]["temperature"] for c in temp_calls if c[0][2]["entity_id"] == "climate.tabs_office"]
        assert trv_temps, "no set_temperature on the TRV"
        assert 16.0 <= trv_temps[0] <= 24.0

    @pytest.mark.asyncio
    async def test_apply_cooling_turns_off_heat_only_trv(self):
        """Regression: heat-only TRVs are still idled in cooling mode."""
        hass = build_hass()

        def _get(eid):
            if eid == "climate.living_trv":
                return _heat_only_state(state="heat")
            return _cool_only_state()

        hass.states.get = MagicMock(side_effect=_get)
        room = make_room(thermostats=["climate.living_trv"], acs=["climate.ac"])
        ctrl = _make_controller(hass, room)
        await ctrl.async_apply(MODE_COOLING, TargetTemps(heat=23.0, cool=24.0), 1.0, current_temp=27.0)

        calls = hass.services.async_call.call_args_list
        # AC cools
        assert any(
            c[0][1] == "set_hvac_mode" and c[0][2] == {"entity_id": "climate.ac", "hvac_mode": "cool"} for c in calls
        )
        # Heat-only TRV must not receive a cool command
        assert not any(
            c[0][1] == "set_hvac_mode"
            and c[0][2].get("entity_id") == "climate.living_trv"
            and c[0][2].get("hvac_mode") == "cool"
            for c in calls
        )


# ---------------------------------------------------------------------------
# Bug 1: idle setpoint inversion for cooling devices
# ---------------------------------------------------------------------------


class TestIdleInversion:
    @pytest.mark.asyncio
    async def test_idle_low_raises_to_max_temp_on_cooling_device(self):
        """idle_action=low on a cooling device writes max_temp, not min_temp."""
        hass = build_hass()
        state = _cool_only_state(state="cool", temperature=20.0)
        hass.states.get = MagicMock(return_value=state)
        devices = [{"entity_id": "climate.tabs", "type": "trv", "idle_action": "low"}]
        await async_idle_device(hass, "climate.tabs", devices, targets=TargetTemps(heat=23.0, cool=24.0))

        calls = hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]
        assert temp_calls, "expected a setpoint write"
        assert temp_calls[0][0][2]["temperature"] == 31.0  # max_temp = no cooling demand

    @pytest.mark.asyncio
    async def test_idle_low_still_lowers_to_min_temp_on_heating_device(self):
        """Regression: heating TRVs keep the original min_temp behavior."""
        hass = build_hass()
        state = _heat_only_state(state="heat", temperature=21.0)
        hass.states.get = MagicMock(return_value=state)
        devices = [{"entity_id": "climate.trv", "type": "trv", "idle_action": "low"}]
        await async_idle_device(hass, "climate.trv", devices, targets=TargetTemps(heat=21.0, cool=None))

        calls = hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]
        assert temp_calls, "expected a setpoint write"
        assert temp_calls[0][0][2]["temperature"] == 5.0  # min_temp = valve closed

    @pytest.mark.asyncio
    async def test_turn_off_defense_in_depth_uses_max_temp_for_cooling(self):
        """Pre-off setpoint on a cooling device goes to max_temp, then off."""
        hass = build_hass()
        state = _cool_only_state(state="cool", temperature=20.0)
        hass.states.get = MagicMock(return_value=state)
        await async_turn_off_climate(hass, "climate.tabs")

        calls = hass.services.async_call.call_args_list
        assert calls[0][0][1] == "set_temperature"
        assert calls[0][0][2]["temperature"] == 31.0  # NOT min_temp (16 = max cooling)
        assert calls[1][0][1] == "set_hvac_mode"
        assert calls[1][0][2]["hvac_mode"] == "off"

    @pytest.mark.asyncio
    async def test_idle_low_fallback_direction_for_cooling(self):
        """Without a usable max_temp, fallback = cool_target + offset (not heat - offset)."""
        hass = build_hass()
        state = _state(
            "cool",
            {"hvac_modes": ["off", "cool"], "min_temp": 16.0, "temperature": 20.0},  # no max_temp
        )
        hass.states.get = MagicMock(return_value=state)
        devices = [{"entity_id": "climate.tabs", "type": "trv", "idle_action": "low"}]
        await async_idle_device(hass, "climate.tabs", devices, targets=TargetTemps(heat=23.0, cool=24.0))

        calls = hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]
        assert temp_calls, "expected a setpoint write"
        assert temp_calls[0][0][2]["temperature"] == 26.0  # cool 24 + offset 2
