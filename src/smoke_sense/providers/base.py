"""Provider interface and registry.

Each provider adapts a public data source to the common tidy format. Providers
register by name so the CLI can resolve `--source` values and default to all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from ..data import Pollutant

_REGISTRY: dict[str, type["AQIProvider"]] = {}


class AQIProvider(ABC):
    """Base class for air-quality data providers."""

    name: str
    supported: set[Pollutant]

    def __init__(self, **kwargs) -> None:
        # Concrete providers accept credentials/sessions via kwargs.
        pass

    @abstractmethod
    def fetch(
        self,
        county_fips: str,
        start: date,
        end: date,
        pollutants: list[Pollutant],
    ) -> pd.DataFrame:
        """Return a `data`-schema DataFrame for the county/range/pollutants."""
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
