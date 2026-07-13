"""Residual heat modeling for thermal mass (underfloor heating, radiators).

After heating stops, the thermal mass (floor screed, radiator body) continues
releasing stored energy into the room.  This module provides functions to
compute the decaying residual heat fraction so that the EKF and MPC can
account for it instead of mis-attributing the continued warming to solar
gain or insulation effects.
"""

from __future__ import annotations

import math

from ..const import HEATING_SYSTEM_PROFILES, RESIDUAL_HEAT_CUTOFF


def compute_residual_heat(
    elapsed_minutes: float,
    system_type: str,
    last_power_fraction: float = 1.0,
    heating_duration_minutes: float = 0.0,
) -> float:
    """Compute normalised residual heat fraction after heating stopped.

    The result is in [0, initial_fraction] and represents the fraction of
    ``beta_h`` (learned heating rate) still being delivered by thermal mass.

    The *charge_fraction* accounts for how long heating was active before
    it stopped — a brief 10-minute run barely warms the screed, whereas
    a 4-hour run fully charges it.

    Args:
        elapsed_minutes: Time since heating stopped (must be >= 0).
        system_type: Key into ``HEATING_SYSTEM_PROFILES`` (e.g. "underfloor").
        last_power_fraction: Power fraction that was active before stopping.
        heating_duration_minutes: How long heating was active before stopping.

    Returns:
        Residual heat fraction in [0, 1].  Returns 0.0 for unknown or empty
        system types (backwards-compatible default).
    """
    profile = HEATING_SYSTEM_PROFILES.get(system_type)
    if not profile or elapsed_minutes < 0:
        return 0.0

    tau = profile["tau_minutes"]
    initial = profile["initial_fraction"]
    tau_charge = profile.get("tau_charge_minutes", tau)

    if tau <= 0:
        return 0.0

    # Charge fraction: how much energy the thermal mass absorbed
    if heating_duration_minutes > 0 and tau_charge > 0:
        charge_fraction = 1.0 - math.exp(-heating_duration_minutes / tau_charge)
    else:
        charge_fraction = 1.0  # assume fully charged if duration unknown

    q = initial * charge_fraction * math.exp(-elapsed_minutes / tau) * last_power_fraction
    return q if q >= RESIDUAL_HEAT_CUTOFF else 0.0


def decay_residual_heat(q_now: float, elapsed_minutes: float, system_type: str) -> float:
    """Decay an already-known residual fraction by *elapsed_minutes* more.

    Used when the absolute time since heating stopped is unknown but the
    current residual value is (e.g. seeding an analytics simulation from
    live tracker state).
    """
    if q_now <= 0:
        return 0.0
    profile = HEATING_SYSTEM_PROFILES.get(system_type)
    if not profile or profile["tau_minutes"] <= 0:
        return 0.0
    q = q_now * math.exp(-max(0.0, elapsed_minutes) / profile["tau_minutes"])
    return q if q >= RESIDUAL_HEAT_CUTOFF else 0.0


def build_residual_series(
    elapsed_minutes: float,
    system_type: str,
    n_blocks: int,
    dt_minutes: float = 5.0,
    last_power_fraction: float = 1.0,
    heating_duration_minutes: float = 0.0,
) -> list[float]:
    """Build a decaying residual heat series for MPC lookahead.

    Each entry represents the q_residual at the start of the corresponding
    time block, evenly spaced by *dt_minutes*.

    Returns:
        List of length *n_blocks* with decaying residual fractions.
    """
    return [
        compute_residual_heat(
            elapsed_minutes + i * dt_minutes,
            system_type,
            last_power_fraction,
            heating_duration_minutes,
        )
        for i in range(n_blocks)
    ]


def get_min_run_blocks(system_type: str, dt_minutes: float = 5.0) -> int:
    """Return the minimum number of MPC blocks for a given heating system.

    Falls back to 2 blocks (the existing default) when the system type is
    unknown or empty.
    """
    profile = HEATING_SYSTEM_PROFILES.get(system_type)
    if not profile or dt_minutes <= 0:
        return 2  # existing default
    return max(2, math.ceil(profile["min_run_minutes"] / dt_minutes))
