"""Electricity price parsing and cost-series building for the economic MPC.

Pure utility module (no HA dependencies).  Normalizes the attribute formats
of the common HA price integrations — Nordpool (raw_today/raw_tomorrow),
Tibber-style (startsAt/total), ENTSO-E / EMHASS-style forecast lists — into
a sorted list of (epoch_ts, price) points, then step-interpolates them into
per-block series for the MPC horizon.

Prices are used RELATIVELY (normalized by their horizon mean), so the unit
(EUR/kWh, ct/kWh, öre …) does not matter.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Attribute names that may hold a list of {time, value} entries
_FORECAST_ATTRS = (
    "raw_today",
    "raw_tomorrow",
    "forecast",
    "prices_today",
    "prices_tomorrow",
    "prices",
    "data",
)
_TIME_KEYS = ("start", "start_time", "startsAt", "datetime", "time", "hour")
_VALUE_KEYS = ("value", "price", "electricity_price", "price_ct_per_kwh", "total")

# COP model clamps
_COP_MIN = 1.0
_COP_MAX = 8.0


def _to_epoch(value: Any) -> float | None:
    """Best-effort conversion of a time field to an epoch timestamp."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _parse_entry(entry: dict) -> tuple[float, float] | None:
    ts: float | None = None
    for k in _TIME_KEYS:
        if k in entry:
            ts = _to_epoch(entry[k])
            break
    if ts is None:
        return None
    for k in _VALUE_KEYS:
        v = entry.get(k)
        if isinstance(v, (int, float)):
            return (ts, float(v))
    return None


def extract_price_points(attributes: dict) -> list[tuple[float, float]]:
    """Extract sorted (epoch_ts, price) points from a price entity's attributes."""
    points: list[tuple[float, float]] = []
    for attr in _FORECAST_ATTRS:
        val = attributes.get(attr)
        if not isinstance(val, list):
            continue
        for entry in val:
            if isinstance(entry, dict):
                parsed = _parse_entry(entry)
                if parsed is not None:
                    points.append(parsed)
    points.sort(key=lambda p: p[0])
    return points


def build_price_series(
    points: list[tuple[float, float]],
    now_ts: float,
    n_blocks: int,
    dt_minutes: float,
) -> list[float] | None:
    """Step-interpolate price points into a per-block series for the horizon.

    Each block takes the price of the last point at or before its start.
    Blocks past the forecast end keep the last known price (persistence).
    Returns None when no point covers the horizon start (stale forecast).
    """
    if not points:
        return None
    series: list[float] = []
    idx = 0
    current: float | None = None
    for i in range(n_blocks):
        block_ts = now_ts + i * dt_minutes * 60.0
        while idx < len(points) and points[idx][0] <= block_ts:
            current = points[idx][1]
            idx += 1
        if current is None:
            # Horizon starts before the first forecast point — treat the
            # whole forecast as unusable rather than guessing backwards.
            return None
        series.append(current)
    return series


def cop_at(t_out: float, cop_at_minus7: float, cop_at_plus7: float) -> float:
    """Heat pump COP at *t_out*, linear between two datasheet points.

    The two points are the COPs at -7°C and +7°C outdoor (any consistent
    supply temperature).  Clamped to [1, 8].
    """
    slope = (cop_at_plus7 - cop_at_minus7) / 14.0
    return max(_COP_MIN, min(_COP_MAX, cop_at_minus7 + slope * (t_out + 7.0)))


def build_cost_series(
    price_series: list[float] | None,
    outdoor_series: list[float],
    cop_at_minus7: float = 0.0,
    cop_at_plus7: float = 0.0,
    *,
    pv_export_active: bool = False,
    pv_free_blocks: int = 2,
) -> list[float] | None:
    """Combine price and COP into a normalized per-block energy-cost series.

    The result multiplies the optimizer's energy term: mean 1.0 over the
    horizon so the comfort/efficiency slider keeps its meaning; cheap /
    high-COP blocks fall below 1, expensive / low-COP blocks rise above it.
    Negative prices survive normalization (heating becomes a credit).

    When *pv_export_active*, the first *pv_free_blocks* blocks cost 0 —
    self-consumed solar surplus is treated as free energy right now.

    Returns None when neither price nor COP information is available
    (optimizer keeps its flat legacy cost).
    """
    n = len(outdoor_series)
    if n == 0:
        return None
    has_cop = cop_at_minus7 > 0.0 and cop_at_plus7 > 0.0
    if price_series is None and not has_cop and not pv_export_active:
        return None

    cost = list(price_series) if price_series is not None else [1.0] * n
    if len(cost) < n:
        cost = cost + [cost[-1]] * (n - len(cost))
    cost = cost[:n]

    if has_cop:
        cost = [c / cop_at(outdoor_series[i], cop_at_minus7, cop_at_plus7) for i, c in enumerate(cost)]

    mean = sum(cost) / n
    if mean > 1e-9:
        cost = [c / mean for c in cost]
    else:
        cost = [1.0] * n

    if pv_export_active:
        for i in range(min(pv_free_blocks, n)):
            cost[i] = 0.0
    return cost
