"""Personhood verification/gate error hierarchy (distinct from schema errors).

``records.PersonhoodSchemaError`` is a *shape* error (a record violated the anti-PII
whitelist or typing). These are *flow* errors raised while admitting or gating a person.
"""

from __future__ import annotations

__all__ = [
    "PersonhoodError",
    "NotPersonError",
    "AlreadyRegisteredError",
    "RevokedError",
    "ExpiredError",
]


class PersonhoodError(Exception):
    """Base class for personhood admission/gate failures."""


class NotPersonError(PersonhoodError):
    """The presentation did not establish a valid, unique EU natural person."""


class AlreadyRegisteredError(PersonhoodError):
    """This person already holds an anchor in this scope (sybil attempt / double-register)."""


class RevokedError(PersonhoodError):
    """The person's anchor is revoked at the pinned epoch."""


class ExpiredError(PersonhoodError):
    """The anchor's validity window does not contain the current time."""
