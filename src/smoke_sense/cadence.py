"""Data cadence (averaging window) selection.

A `Cadence` is a named averaging window. Its `.minutes` value is both the
PurpleAir `average` query value and the `agg_window` recorded in the data.
"""

from __future__ import annotations

from enum import Enum


class Cadence(str, Enum):
    RAW = "RAW"            # real-time, ~2 min
    TEN_MIN = "TEN_MIN"
    THIRTY_MIN = "THIRTY_MIN"
    HOURLY = "HOURLY"
    SIX_HOURLY = "SIX_HOURLY"
    DAILY = "DAILY"

    @property
    def minutes(self) -> int:
        return _CADENCE_MINUTES[self]


_CADENCE_MINUTES: dict[Cadence, int] = {
    Cadence.RAW: 0,
    Cadence.TEN_MIN: 10,
    Cadence.THIRTY_MIN: 30,
    Cadence.HOURLY: 60,
    Cadence.SIX_HOURLY: 360,
    Cadence.DAILY: 1440,
}


def resolve_cadence(supported: list[int], requested: int) -> int:
    """Finest supported window no coarser than `requested`; else the finest.

    Returns the largest supported window <= requested (so data is never coarser
    than asked) or, if the provider cannot go that fine, its finest window.
    """
    candidates = [c for c in supported if c <= requested]
    return max(candidates) if candidates else min(supported)
