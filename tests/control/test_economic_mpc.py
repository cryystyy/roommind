"""Tests for the economic price-aware MPC (price parsing, COP, load shifting)."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.roommind.const import MODE_HEATING, MODE_IDLE
from custom_components.roommind.control.mpc_optimizer import MPCOptimizer
from custom_components.roommind.control.thermal_model import RCModel
from custom_components.roommind.utils.price_utils import (
    build_cost_series,
    build_price_series,
    cop_at,
    extract_price_points,
)

# ---------------------------------------------------------------------------
# Price parsing
# ---------------------------------------------------------------------------


class TestExtractPricePoints:
    def test_nordpool_raw_format(self):
        attrs = {
            "raw_today": [
                {"start": "2026-07-13T10:00:00+00:00", "end": "2026-07-13T11:00:00+00:00", "value": 0.12},
                {"start": "2026-07-13T11:00:00+00:00", "end": "2026-07-13T12:00:00+00:00", "value": 0.30},
            ],
            "raw_tomorrow": [
                {"start": "2026-07-14T10:00:00+00:00", "end": "2026-07-14T11:00:00+00:00", "value": 0.08},
            ],
        }
        points = extract_price_points(attrs)
        assert len(points) == 3
        assert points[0][1] == 0.12
        assert points[-1][1] == 0.08
        assert points == sorted(points)

    def test_tibber_style(self):
        attrs = {"forecast": [{"startsAt": "2026-07-13T10:00:00+00:00", "total": 0.25}]}
        points = extract_price_points(attrs)
        assert points == [(datetime(2026, 7, 13, 10, tzinfo=UTC).timestamp(), 0.25)]

    def test_datetime_objects(self):
        dt = datetime(2026, 7, 13, 10, tzinfo=UTC)
        points = extract_price_points({"raw_today": [{"start": dt, "value": 0.2}]})
        assert points == [(dt.timestamp(), 0.2)]

    def test_garbage_ignored(self):
        attrs = {"raw_today": [{"start": "not-a-date", "value": 1}, {"value": 2}, "nope", 5]}
        assert extract_price_points(attrs) == []
        assert extract_price_points({}) == []


class TestBuildPriceSeries:
    def test_step_interpolation_and_padding(self):
        t0 = 1_000_000.0
        points = [(t0, 0.10), (t0 + 3600, 0.30)]
        # 24 blocks x 5min = 2h starting at t0
        series = build_price_series(points, t0, 24, 5.0)
        assert series is not None
        assert series[0] == 0.10
        assert series[11] == 0.10  # last block of hour 1
        assert series[12] == 0.30  # first block of hour 2
        assert series[23] == 0.30  # padded with last known price

    def test_no_coverage_returns_none(self):
        t0 = 1_000_000.0
        assert build_price_series([], t0, 12, 5.0) is None
        # Forecast starts only in the future -> unusable
        assert build_price_series([(t0 + 7200, 0.2)], t0, 12, 5.0) is None


class TestCop:
    def test_linear_between_datasheet_points(self):
        assert cop_at(-7.0, 2.5, 4.0) == 2.5
        assert cop_at(7.0, 2.5, 4.0) == 4.0
        assert cop_at(0.0, 2.5, 4.0) == 2.5 + 1.5 / 2
        assert cop_at(35.0, 2.5, 4.0) <= 8.0  # clamped
        assert cop_at(-30.0, 2.5, 4.0) >= 1.0  # clamped


class TestBuildCostSeries:
    def test_none_without_inputs(self):
        assert build_cost_series(None, [10.0] * 4) is None

    def test_normalized_to_mean_one(self):
        cost = build_cost_series([0.1, 0.1, 0.3, 0.3], [10.0] * 4)
        assert cost is not None
        assert abs(sum(cost) / 4 - 1.0) < 1e-9
        assert cost[0] < 1.0 < cost[2]

    def test_cop_prefers_warm_hours(self):
        # Flat price, outdoor warming from -7 to +7: later blocks cheaper
        cost = build_cost_series([1.0] * 4, [-7.0, 0.0, 5.0, 7.0], 2.5, 4.0)
        assert cost is not None
        assert cost[0] > cost[-1]

    def test_pv_export_zeroes_now(self):
        cost = build_cost_series([0.2] * 6, [10.0] * 6, pv_export_active=True)
        assert cost is not None
        assert cost[0] == 0.0
        assert cost[1] == 0.0
        assert cost[2] > 0.0


# ---------------------------------------------------------------------------
# Load shifting behavior
# ---------------------------------------------------------------------------


def _optimizer(**over):
    # C=1 normalized model like the learned EKF export; slow room (tau ~20h)
    defaults = dict(
        model=RCModel(C=1.0, U=0.05, Q_heat=2.0, Q_cool=2.0, Q_solar=0.0),
        can_heat=True,
        can_cool=False,
        w_comfort=7.0,
        w_energy=3.0,
        min_run_blocks=2,
    )
    defaults.update(over)
    return MPCOptimizer(**defaults)


class TestLoadShifting:
    def test_cheap_now_preheats_expensive_later_coasts(self):
        """Same physical scenario, opposite price shapes -> opposite block-0 action.

        Room slightly above target and slowly drifting toward it. With cheap
        energy NOW (price spike later) the optimizer should pre-heat; with
        expensive energy NOW (cheap later) it should idle and wait.
        """
        n = 24
        outdoor = [0.0] * n
        heat_t = [21.0] * n
        cool_t = [25.0] * n
        T_room = 21.3  # above target, drifting down slowly

        cheap_now = build_cost_series([0.05] * 12 + [0.60] * 12, outdoor)
        expensive_now = build_cost_series([0.60] * 12 + [0.05] * 12, outdoor)

        plan_cheap = _optimizer().optimize(T_room, outdoor, heat_t, cool_target_series=cool_t, cost_series=cheap_now)
        plan_expensive = _optimizer().optimize(
            T_room, outdoor, heat_t, cool_target_series=cool_t, cost_series=expensive_now
        )

        assert plan_cheap.actions[0] == MODE_HEATING, "cheap-now should pre-heat"
        assert plan_expensive.actions[0] == MODE_IDLE, "expensive-now should coast"

    def test_no_cost_series_keeps_legacy_energy_term(self):
        """Without a cost series the legacy flat term applies (unchanged path)."""
        n = 24
        outdoor = [0.0] * n
        heat_t = [21.0] * n
        cool_t = [25.0] * n
        plan = _optimizer().optimize(20.0, outdoor, heat_t, cool_target_series=cool_t)
        assert plan.actions[0] == MODE_HEATING  # clearly below target

    def test_flat_cost_still_heats_when_cold(self):
        """A flat economic cost must not stop the room from being heated."""
        n = 24
        outdoor = [0.0] * n
        heat_t = [21.0] * n
        cool_t = [25.0] * n
        plan = _optimizer().optimize(19.5, outdoor, heat_t, cool_target_series=cool_t, cost_series=[1.0] * n)
        assert plan.actions[0] == MODE_HEATING
