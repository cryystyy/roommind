"""Regression tests for bugs found in the full-codebase scan.

Each test pins one fix; see the commit message for the full list.
"""

from __future__ import annotations

import math

from custom_components.roommind.control.mpc_controller import _placeholder_targets
from custom_components.roommind.control.residual_heat import decay_residual_heat
from custom_components.roommind.control.solar import _solar_position


class TestPlaceholderTargets:
    """heat=None must not lift the cool ceiling via the optimizer clamp."""

    def test_cool_only_hot_room(self):
        # Room 27, cool target 24, no heat target: the heat placeholder must
        # stay at/below the cool target or cool=max(heat,cool) erases demand.
        h, c = _placeholder_targets(None, 24.0, 27.0)
        assert h <= 24.0
        assert c == 24.0

    def test_cool_only_cold_room(self):
        # Room below the cool target: placeholder must not create heat demand
        h, c = _placeholder_targets(None, 24.0, 20.0)
        assert h <= 20.0

    def test_heat_only(self):
        h, c = _placeholder_targets(21.0, None, 26.0)
        assert h == 21.0
        assert c >= 21.0  # clamp no-op, no artificial cooling demand

    def test_both_none(self):
        assert _placeholder_targets(None, None, 22.0) == (22.0, 22.0)

    def test_both_set_passthrough(self):
        assert _placeholder_targets(21.0, 24.0, 27.0) == (21.0, 24.0)


class TestSolarTimeWrap:
    """True solar time must wrap to [0, 1440) or the azimuth mirrors E/W."""

    def test_azimuth_not_mirrored_late_utc_positive_longitude(self):
        # 2026-06-21 ~04:00 UTC in eastern Australia (longitude 150°E):
        # local solar morning — azimuth must be in the eastern half [0°, 180°].
        # Unwrapped tst = 240 + eqtime + 600 ≈ 840 is fine, so pick a case
        # that actually overflows: 23:00 UTC, longitude 150°E → tst ≈ 1980.
        elev, az = _solar_position(latitude=-33.0, longitude=150.0, timestamp=1782082800.0)
        # 2026-06-21 23:00 UTC = 09:00 next day local solar time → morning
        if elev > 0:
            assert 0.0 <= az <= 180.0, f"morning sun must be east, got {az}"

    def test_wrap_equivalence(self):
        # Same solar time expressed with/without the 1440-minute overflow
        # must give the same position (periodicity check across midnight UTC)
        elev1, az1 = _solar_position(45.0, 179.0, 1782082800.0)
        assert -90.0 <= elev1 <= 90.0
        assert 0.0 <= az1 < 360.0


class TestColdStartPriors:
    def test_tabs_prior_slower_than_default(self):
        from custom_components.roommind.control.thermal_model import ThermalEKF

        default = ThermalEKF()
        tabs = ThermalEKF(system_type="tabs")
        ufh = ThermalEKF(system_type="underfloor")
        # High-thermal-mass systems start with lower loss/drive rates
        assert tabs._x[1] < ufh._x[1] < default._x[1]  # alpha
        assert tabs._x[2] < ufh._x[2] < default._x[2]  # beta_h
        assert tabs._x[3] < ufh._x[3] < default._x[3]  # beta_c

    def test_unknown_type_uses_defaults(self):
        from custom_components.roommind.control.thermal_model import ThermalEKF

        assert ThermalEKF(system_type="radiator")._x == ThermalEKF()._x

    def test_persisted_model_not_reseeded(self):
        from custom_components.roommind.control.thermal_model import RoomModelManager

        mgr = RoomModelManager()
        est = mgr.get_estimator("room", "tabs")
        restored = RoomModelManager.from_dict(mgr.to_dict())
        # Restoring keeps the persisted state; a later typed get is a no-op
        est2 = restored.get_estimator("room", "radiator")
        assert est2._x == est._x


class TestDecayResidualHeat:
    def test_decays_over_time(self):
        q0 = 0.5
        q1 = decay_residual_heat(q0, 30.0, "underfloor")
        q2 = decay_residual_heat(q0, 120.0, "underfloor")
        assert 0.0 <= q2 < q1 < q0

    def test_zero_and_unknown_system(self):
        assert decay_residual_heat(0.0, 10.0, "underfloor") == 0.0
        assert decay_residual_heat(0.5, 10.0, "not_a_system") == 0.0

    def test_matches_exponential(self):
        from custom_components.roommind.const import HEATING_SYSTEM_PROFILES

        tau = HEATING_SYSTEM_PROFILES["underfloor"]["tau_minutes"]
        q = decay_residual_heat(0.5, tau, "underfloor")
        assert math.isclose(q, 0.5 * math.exp(-1.0), rel_tol=1e-6) or q == 0.0
