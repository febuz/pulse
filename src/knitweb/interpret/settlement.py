"""Mining→Settlement boundary crossing contract for the Interpretation Lobe.

Only the three fields in :data:`CROSSING_FIELDS` may cross from the Mining stage
into the Settlement stage.  Any payload containing a field from
:data:`BANNED_FIELDS` (PII, model weights, raw text) raises
:class:`BoundaryViolation` before any settlement logic runs.

This is a pure data-transform function with no I/O, so it is trivially
property-testable and adds no import-time overhead.
"""

from __future__ import annotations

__all__ = [
    "CROSSING_FIELDS",
    "BANNED_FIELDS",
    "BoundaryViolation",
    "cross_boundary",
]

CROSSING_FIELDS: frozenset[str] = frozenset({
    "result_cid",
    "provenance_chain",
    "verdict",
})

BANNED_FIELDS: frozenset[str] = frozenset({
    "embedding",
    "weights",
    "raw_text",
    "email",
    "ip",
})


class BoundaryViolation(ValueError):
    """Raised when a banned field is present in the payload."""


def cross_boundary(payload: dict) -> dict:
    """Strip ``payload`` to only the allowed crossing fields.

    Raises :class:`BoundaryViolation` immediately if any :data:`BANNED_FIELDS`
    key is present — even if the crossing fields are also present.  The caller
    must sanitise the payload BEFORE calling this function.

    Returns a new dict containing only the keys in :data:`CROSSING_FIELDS` that
    are actually present in ``payload``.
    """
    violations = BANNED_FIELDS & payload.keys()
    if violations:
        raise BoundaryViolation(
            f"payload contains banned fields: {sorted(violations)!r}"
        )
    return {k: v for k, v in payload.items() if k in CROSSING_FIELDS}
