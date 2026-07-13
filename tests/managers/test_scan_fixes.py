"""Regression tests for manager bugs found in the full-codebase scan."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.roommind.managers.mold_manager import MoldManager


@pytest.fixture
def mm():
    hass = MagicMock()
    return MoldManager(hass)


def _settings(**over):
    s = {
        "mold_detection_enabled": True,
        "mold_prevention_enabled": True,
        "mold_notifications_enabled": False,
        "mold_humidity_threshold": 70,
        "mold_sustained_minutes": 0,
    }
    s.update(over)
    return s


@pytest.mark.asyncio
async def test_prevention_holds_through_hysteresis_band(mm):
    """Once active, prevention stays applied while surface RH sits in the
    release band (warning-hysteresis .. warning) instead of dropping instantly."""
    with patch(
        "custom_components.roommind.managers.mold_manager.calculate_mold_risk",
        return_value=("warning", 75.0),
    ):
        r1 = await mm.evaluate("room", "Room", 20.0, 70.0, 5.0, _settings())
    assert r1.prevention_active
    assert r1.prevention_delta > 0

    # Surface RH dips just below warning (in the hysteresis band): must HOLD
    with patch(
        "custom_components.roommind.managers.mold_manager.calculate_mold_risk",
        return_value=("ok", 68.0),
    ):
        r2 = await mm.evaluate("room", "Room", 20.0, 60.0, 5.0, _settings())
    assert r2.prevention_active, "prevention must hold inside the hysteresis band"
    assert r2.prevention_delta > 0

    # Surface RH below release threshold (warning - hysteresis): deactivate
    with patch(
        "custom_components.roommind.managers.mold_manager.calculate_mold_risk",
        return_value=("ok", 55.0),
    ):
        r3 = await mm.evaluate("room", "Room", 20.0, 50.0, 5.0, _settings())
    assert not r3.prevention_active


@pytest.mark.asyncio
async def test_detection_only_notification_dismissed_on_clear(mm):
    """Risk notifications must be dismissed on clear even when prevention
    never activated (detection-only setups)."""
    with patch(
        "custom_components.roommind.managers.mold_manager.calculate_mold_risk",
        return_value=("warning", 75.0),
    ):
        await mm.evaluate("room", "Room", 20.0, 75.0, 5.0, _settings(mold_prevention_enabled=False))

    with (
        patch(
            "custom_components.roommind.managers.mold_manager.calculate_mold_risk",
            return_value=("ok", 55.0),
        ),
        patch("custom_components.roommind.managers.mold_manager.dismiss_mold_notification") as dismiss,
    ):
        await mm.evaluate("room", "Room", 20.0, 50.0, 5.0, _settings(mold_prevention_enabled=False))
    assert dismiss.called, "risk notification must be dismissed without prevention ever active"


def test_history_header_migration_on_append(tmp_path):
    """Appending to a pre-schema-change history file migrates the header
    instead of writing misaligned columns."""
    from custom_components.roommind.utils.history_store import DETAIL_FIELDS, HistoryStore

    store = HistoryStore(str(tmp_path))
    # Simulate a pre-1.7.2 file: same columns minus the newer ones
    old_fields = [f for f in DETAIL_FIELDS if f not in ("cover_reason", "occupancy")]
    path = tmp_path / "room1_history.csv"
    path.write_text(",".join(old_fields) + "\n" + ",".join(["1000"] + ["1"] * (len(old_fields) - 1)) + "\n")

    store._append_history("room1", [{"timestamp": 2000, "mode": "idle", "room_temp": 21.5}])

    rows = store.read_history("room1")
    assert len(rows) == 2
    # Old row keeps its values under the right column names
    assert rows[0]["timestamp"] == "1000"
    # New row lands in the right columns
    assert rows[1]["timestamp"] == "2000"
    assert rows[1]["room_temp"] == "21.5"
    assert rows[1]["mode"] == "idle"


class TestSlabChargeFraction:
    def test_charges_while_heating_and_decays_after(self):
        import time
        from unittest.mock import patch

        from custom_components.roommind.managers.residual_heat_tracker import ResidualHeatTracker

        tr = ResidualHeatTracker()
        t0 = 1_000_000.0
        with patch("time.time", return_value=t0):
            tr.update("r", "heating", 1.0, "idle")
        with patch("time.time", return_value=t0 + 3600):
            mid = tr.get_charge_fraction("r", "tabs", "heating")
        with patch("time.time", return_value=t0 + 4 * 3600):
            full = tr.get_charge_fraction("r", "tabs", "heating")
            tr.update("r", "idle", 0.0, "heating")  # heating stops
        with patch("time.time", return_value=t0 + 5 * 3600):
            decayed1 = tr.get_charge_fraction("r", "tabs", "idle")
        with patch("time.time", return_value=t0 + 9 * 3600):
            decayed2 = tr.get_charge_fraction("r", "tabs", "idle")
        assert 0.0 < mid < full <= 1.0
        assert full > decayed1 > decayed2 >= 0.0
        _ = time  # keep import used

    def test_none_for_unknown_system(self):
        from custom_components.roommind.managers.residual_heat_tracker import ResidualHeatTracker

        tr = ResidualHeatTracker()
        assert tr.get_charge_fraction("r", "", "idle") is None

    def test_zero_when_never_heated(self):
        from custom_components.roommind.managers.residual_heat_tracker import ResidualHeatTracker

        tr = ResidualHeatTracker()
        assert tr.get_charge_fraction("r", "tabs", "idle") == 0.0
