"""Air-quality data providers.

Importing this package registers all concrete providers.
"""

from . import aqs, purpleair  # noqa: F401  (import side effect: registration)
from .base import AQIProvider, all_providers, get_provider, register

__all__ = ["AQIProvider", "all_providers", "get_provider", "register"]
