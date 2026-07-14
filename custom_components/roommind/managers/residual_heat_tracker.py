"""Residual heat transition tracking for RoomMind."""

from __future__ import annotations

import math
import time

from ..const import HEATING_SYSTEM_PROFILES, MODE_COOLING, MODE_HEATING
from ..control.residual_heat import compute_residual_heat

# Cooling ("cold charge") state is cleared once it is unquestionably stale.
# Unlike heating state, which is cleared when the coordinator-computed
# q_residual reaches 0.0, cooling staleness is derived from elapsed time
# using the slowest profile: after 5 tau the stored charge has decayed to
# exp(-5) ~ 0.7 %, below anything get_charge_fraction can meaningfully
# display.  (The signed q_residual passed to update() is not used for
# cooling cleanup: it reads 0.0 when the cold_residual_enabled gate is off
# or when heating state is more recent, neither of which means the cold
# charge is stale.)
_COOL_STATE_MAX_AGE_MINUTES = 5.0 * max(p["tau_minutes"] for p in HEATING_SYSTEM_PROFILES.values())


class ResidualHeatTracker:
    """Tracks heating and cooling on/off transitions per room.

    Heating and cooling transitions both feed the signed residual model
    (``get_q_residual``: positive = stored heat, negative = stored cold)
    and the thermal-mass state of charge (``get_charge_fraction``).
    """

    def __init__(self) -> None:
        self._off_since: dict[str, float] = {}
        self._off_power: dict[str, float] = {}
        self._on_since: dict[str, float] = {}
        # Mirrored cooling-transition state ("cold charge")
        self._cool_off_since: dict[str, float] = {}
        self._cool_off_power: dict[str, float] = {}
        self._cool_on_since: dict[str, float] = {}

    def get_q_residual(self, area_id: str, system_type: str, previous_mode: str) -> float:
        """Compute the signed residual fraction from previous cycle state.

        Positive = residual heat after a heating run (fraction of the
        heating rate, unchanged legacy semantics); negative = residual
        cold after a cooling run (fraction of the cooling rate).  Each
        direction is suppressed while its own mode is still commanded
        (``previous_mode``); when residual state exists for both
        directions (season change), the direction with the more recent
        off-transition wins — the same rule as ``get_charge_fraction``.
        """
        if not system_type:
            return 0.0
        heat_off = self._off_since.get(area_id) if previous_mode != MODE_HEATING else None
        cool_off = self._cool_off_since.get(area_id) if previous_mode != MODE_COOLING else None
        now = time.time()
        if heat_off is not None and (cool_off is None or heat_off >= cool_off):
            elapsed = (now - heat_off) / 60.0
            heat_dur = (heat_off - self._on_since.get(area_id, heat_off)) / 60.0
            last_pf = self._off_power.get(area_id, 1.0)
            return compute_residual_heat(elapsed, system_type, last_pf, heat_dur)
        if cool_off is not None:
            elapsed = (now - cool_off) / 60.0
            cool_dur = (cool_off - self._cool_on_since.get(area_id, cool_off)) / 60.0
            last_pf = self._cool_off_power.get(area_id, 1.0)
            return -compute_residual_heat(elapsed, system_type, last_pf, cool_dur)
        return 0.0

    def update(
        self, area_id: str, mode: str, power_fraction: float, previous_mode: str, q_residual: float = 0.0
    ) -> None:
        """Update heating and cooling transition state based on current mode.

        ``q_residual`` must be the raw (ungated) signed value from
        ``get_q_residual``.  Heating state is cleared once it has decayed
        to 0.0; a negative value (residual cold more recent) keeps heating
        state as-is.  Cooling state is cleared once older than
        ``_COOL_STATE_MAX_AGE_MINUTES``.
        """
        if mode == MODE_HEATING:
            self._off_since.pop(area_id, None)
            self._off_power[area_id] = power_fraction
            if previous_mode != MODE_HEATING:
                self._on_since[area_id] = time.time()
        elif previous_mode == MODE_HEATING:
            self._off_since[area_id] = time.time()
        elif q_residual == 0.0 and area_id in self._off_since:
            self._off_since.pop(area_id, None)
            self._off_power.pop(area_id, None)
            self._on_since.pop(area_id, None)

        if mode == MODE_COOLING:
            self._cool_off_since.pop(area_id, None)
            self._cool_off_power[area_id] = power_fraction
            if previous_mode != MODE_COOLING:
                self._cool_on_since[area_id] = time.time()
        elif previous_mode == MODE_COOLING:
            self._cool_off_since[area_id] = time.time()
        elif (
            area_id in self._cool_off_since
            and (time.time() - self._cool_off_since[area_id]) / 60.0 > _COOL_STATE_MAX_AGE_MINUTES
        ):
            self._cool_off_since.pop(area_id, None)
            self._cool_off_power.pop(area_id, None)
            self._cool_on_since.pop(area_id, None)

    def get_charge_fraction(self, area_id: str, system_type: str, current_mode: str) -> float | None:
        """Normalized thermal-mass state of charge (0-1) for slow systems.

        While a run is active (heating or cooling): how charged the mass is
        so far this run (saturating with tau_charge).  After a run: the
        stored fraction decaying with tau.  Both directions are tracked with
        the same profile constants — heating ("warm charge") and cooling
        ("cold charge"); the returned magnitude carries no sign.  When
        residual state exists for both directions (season change), the
        direction with the more recent off-transition wins.  None when the
        system type has no residual-heat profile (e.g. plain radiators with
        tau=0 still get a value; unknown/"" types return None).
        """
        profile = HEATING_SYSTEM_PROFILES.get(system_type)
        if not profile or profile["tau_minutes"] <= 0:
            return None
        tau = profile["tau_minutes"]
        tau_charge = profile.get("tau_charge_minutes", tau)
        now = time.time()
        if current_mode == MODE_HEATING and area_id in self._on_since:
            minutes = (now - self._on_since[area_id]) / 60.0
            pf = max(self._off_power.get(area_id, 1.0), 0.0)
            return min(1.0, (1.0 - math.exp(-minutes / tau_charge)) * pf)
        if current_mode == MODE_COOLING and area_id in self._cool_on_since:
            minutes = (now - self._cool_on_since[area_id]) / 60.0
            pf = max(self._cool_off_power.get(area_id, 1.0), 0.0)
            return min(1.0, (1.0 - math.exp(-minutes / tau_charge)) * pf)
        heat_off = self._off_since.get(area_id)
        cool_off = self._cool_off_since.get(area_id)
        if heat_off is not None and (cool_off is None or heat_off >= cool_off):
            return self._decayed_charge(
                heat_off,
                self._on_since.get(area_id, heat_off),
                self._off_power.get(area_id, 1.0),
                tau,
                tau_charge,
                now,
            )
        if cool_off is not None:
            return self._decayed_charge(
                cool_off,
                self._cool_on_since.get(area_id, cool_off),
                self._cool_off_power.get(area_id, 1.0),
                tau,
                tau_charge,
                now,
            )
        return 0.0

    @staticmethod
    def _decayed_charge(
        off_since: float, on_since: float, off_power: float, tau: float, tau_charge: float, now: float
    ) -> float:
        """Stored charge from a finished run, decayed since the off-transition."""
        elapsed = (now - off_since) / 60.0
        run_dur = (off_since - on_since) / 60.0
        pf = max(off_power, 0.0)
        charge = (1.0 - math.exp(-run_dur / tau_charge)) if run_dur > 0 else 1.0
        return min(1.0, charge * pf * math.exp(-max(elapsed, 0.0) / tau))

    def remove_room(self, area_id: str) -> None:
        """Clean up state for a removed room."""
        self._off_since.pop(area_id, None)
        self._off_power.pop(area_id, None)
        self._on_since.pop(area_id, None)
        self._cool_off_since.pop(area_id, None)
        self._cool_off_power.pop(area_id, None)
        self._cool_on_since.pop(area_id, None)

    def clear_room(self, area_id: str) -> None:
        """Clear state for a room (thermal reset)."""
        self.remove_room(area_id)

    def clear_all(self) -> None:
        """Clear state for all rooms."""
        self._off_since.clear()
        self._off_power.clear()
        self._on_since.clear()
        self._cool_off_since.clear()
        self._cool_off_power.clear()
        self._cool_on_since.clear()
