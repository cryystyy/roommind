"""Tests for _resolve_display_direction (display-only cooling/heating orientation).

Regression context: a cool-only Rehau TABS zone (hvac_modes [off, cool] in
summer) in Auto mode was inferred as "heating" on a mild evening because the
outdoor heuristic ran before device capability was considered (outdoor 24.3
<= heat_target 25 -> "heating"), flipping the panel headline and the
Analytics target forecast to the heat-side eco target (23) overnight.
"""

from __future__ import annotations

from custom_components.roommind.const import TargetTemps

from .conftest import _create_coordinator


def _direction(coordinator, **kw):
    defaults = dict(
        room={"climate_mode": "auto"},
        display_mode="idle",
        commanded_mode="idle",
        targets=TargetTemps(heat=25.0, cool=25.0),
        current_temp=24.6,
        can_heat=None,
        can_cool=None,
    )
    defaults.update(kw)
    return coordinator._resolve_display_direction(
        defaults["room"],
        defaults["display_mode"],
        defaults["commanded_mode"],
        defaults["targets"],
        defaults["current_temp"],
        can_heat=defaults["can_heat"],
        can_cool=defaults["can_cool"],
    )


class TestDisplayDirectionCapability:
    def test_cool_only_device_never_heating(self, hass, mock_config_entry):
        """The live regression: auto+idle, outdoor below heat target, but the
        device can only cool -> direction must be cooling."""
        c = _create_coordinator(hass, mock_config_entry)
        c.outdoor_temp_effective = 24.3
        assert _direction(c, can_heat=False, can_cool=True) == "cooling"

    def test_heat_only_device_never_cooling(self, hass, mock_config_entry):
        c = _create_coordinator(hass, mock_config_entry)
        c.outdoor_temp_effective = 30.0
        assert (
            _direction(
                c,
                targets=TargetTemps(heat=21.0, cool=24.0),
                current_temp=25.0,
                can_heat=True,
                can_cool=False,
            )
            == "heating"
        )

    def test_dual_capability_falls_through_to_outdoor(self, hass, mock_config_entry):
        """Both capabilities present: legacy outdoor inference unchanged."""
        c = _create_coordinator(hass, mock_config_entry)
        c.outdoor_temp_effective = 30.0
        assert (
            _direction(
                c,
                targets=TargetTemps(heat=21.0, cool=24.0),
                can_heat=True,
                can_cool=True,
            )
            == "cooling"
        )
        c.outdoor_temp_effective = 5.0
        assert (
            _direction(
                c,
                targets=TargetTemps(heat=21.0, cool=24.0),
                can_heat=True,
                can_cool=True,
            )
            == "heating"
        )

    def test_capability_unknown_keeps_legacy_behavior(self, hass, mock_config_entry):
        """can_heat/can_cool None (older callers): outdoor heuristic as before."""
        c = _create_coordinator(hass, mock_config_entry)
        c.outdoor_temp_effective = 24.3
        # outdoor <= heat target -> heating (the legacy, capability-blind answer)
        assert _direction(c) == "heating"

    def test_explicit_mode_still_wins(self, hass, mock_config_entry):
        c = _create_coordinator(hass, mock_config_entry)
        c.outdoor_temp_effective = 24.3
        assert _direction(c, display_mode="cooling", can_heat=True, can_cool=False) == "cooling"
        assert (
            _direction(c, room={"climate_mode": "heat_only"}, can_heat=False, can_cool=True)
            == "heating"
        )
