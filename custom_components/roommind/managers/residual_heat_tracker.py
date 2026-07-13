"""Residual heat transition tracking for RoomMind."""

from __future__ import annotations

import math
import time

from ..const import HEATING_SYSTEM_PROFILES, MODE_HEATING
from ..control.residual_heat import compute_residual_heat


class ResidualHeatTracker:
    """Tracks heating on/off transitions and computes residual heat."""

    def __init__(self) -> None:
        self._off_since: dict[str, float] = {}
        self._off_power: dict[str, float] = {}
        self._on_since: dict[str, float] = {}

    def get_q_residual(self, area_id: str, system_type: str, previous_mode: str) -> float:
        """Compute residual heat from previous cycle state."""
        if not system_type or area_id not in self._off_since or previous_mode == MODE_HEATING:
            return 0.0
        elapsed = (time.time() - self._off_since[area_id]) / 60.0
        heat_dur = (self._off_since[area_id] - self._on_since.get(area_id, self._off_since[area_id])) / 60.0
        last_pf = self._off_power.get(area_id, 1.0)
        return compute_residual_heat(elapsed, system_type, last_pf, heat_dur)

    def update(
        self, area_id: str, mode: str, power_fraction: float, previous_mode: str, q_residual: float = 0.0
    ) -> None:
        """Update heating transition state based on current mode."""
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

    def get_charge_fraction(self, area_id: str, system_type: str, current_mode: str) -> float | None:
        """Normalized thermal-mass state of charge (0-1) for slow systems.

        While heating: how charged the mass is so far this run (saturating
        with tau_charge).  After heating: the stored fraction decaying with
        tau.  Heating charge only — the tracker does not yet follow cooling
        transitions ("cold charge").  None when the system type has no
        residual-heat profile (e.g. plain radiators with tau=0 still get a
        value; unknown/"" types return None).
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
        if area_id in self._off_since:
            elapsed = (now - self._off_since[area_id]) / 60.0
            heat_dur = (self._off_since[area_id] - self._on_since.get(area_id, self._off_since[area_id])) / 60.0
            pf = max(self._off_power.get(area_id, 1.0), 0.0)
            charge = (1.0 - math.exp(-heat_dur / tau_charge)) if heat_dur > 0 else 1.0
            return min(1.0, charge * pf * math.exp(-max(elapsed, 0.0) / tau))
        return 0.0

    def remove_room(self, area_id: str) -> None:
        """Clean up state for a removed room."""
        self._off_since.pop(area_id, None)
        self._off_power.pop(area_id, None)
        self._on_since.pop(area_id, None)

    def clear_room(self, area_id: str) -> None:
        """Clear state for a room (thermal reset)."""
        self.remove_room(area_id)

    def clear_all(self) -> None:
        """Clear state for all rooms."""
        self._off_since.clear()
        self._off_power.clear()
        self._on_since.clear()
