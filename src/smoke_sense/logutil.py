"""Small logging helpers shared by providers."""

from __future__ import annotations

from collections.abc import Iterable


def redact(params: dict, secret_keys: Iterable[str]) -> dict:
    """Return a copy of `params` with `secret_keys` values replaced by '***'."""
    secret = set(secret_keys)
    return {k: ("***" if k in secret else v) for k, v in params.items()}
