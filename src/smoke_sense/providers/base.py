"""Provider interface and registry.

Each provider adapts a public data source to the common tidy format. Providers
register by name so the CLI can resolve `--source` values and default to all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date

import pandas as pd

from ..cadence import resolve_cadence
from ..data import Pollutant

_REGISTRY: dict[str, type["AQIProvider"]] = {}


class AQIProvider(ABC):
    """Base class for air-quality data providers."""

    name: str
    supported: set[Pollutant]
    supported_cadences: list[int]

    def __init__(self, **kwargs) -> None:
        # Concrete providers accept credentials/sessions via kwargs.
        pass

    def resolve_cadence(self, requested: int) -> int:
        """Actual cadence this provider will use for a requested window."""
        return resolve_cadence(self.supported_cadences, requested)

    @abstractmethod
    def fetch(
        self,
        county_fips: str,
        start: date,
        end: date,
        pollutants: list[Pollutant],
        cadence: int = 60,
    ) -> Iterator[pd.DataFrame]:
        """Yield `data`-schema DataFrame chunks for the county/range/pollutants."""
        raise NotImplementedError


def register(cls: type[AQIProvider]) -> type[AQIProvider]:
    """Class decorator registering a provider by its `name`."""
    _REGISTRY[cls.name] = cls
    return cls


def all_providers() -> list[str]:
    """Return the names of all registered providers, sorted."""
    return sorted(_REGISTRY)


def get_provider(name: str, **kwargs) -> AQIProvider:
    """Construct a registered provider by name."""
    if name not in _REGISTRY:
        raise KeyError(f"unknown provider: {name!r} (have {all_providers()})")
    return _REGISTRY[name](**kwargs)
