"""Fiber taxonomy — semantic categorisation for Knitweb synaptic bundles.

A *fiber* is the top-level semantic container of a bundle (e.g. ``data``,
``chem``, ``academic``).  Domains are sub-tags inside that fiber
(e.g. ``governance``, ``organic-chemistry``).  This module provides the
enumerated taxonomy, normalisation helpers, and relation factories so that
fiber/domain metadata can travel inside the existing bytecode format as ordinary
relations, without bumping the bundle version.

The taxonomy is intentionally broad so it can be extended by community vote
when agent ports are opened; the enum is the default, not the limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .bytecode import Relation

__all__ = [
    "Fiber",
    "FIBER_PREDICATE",
    "DOMAIN_PREDICATE",
    "normalize_fiber",
    "normalize_domain",
    "fiber_relations",
    "FiberMeta",
]

FIBER_PREDICATE = "hasFiber"
DOMAIN_PREDICATE = "hasDomain"


class Fiber(str, Enum):
    """Default fiber taxonomy."""

    ACADEMIC = "academic"
    SCIENCE = "science"
    DATA = "data"
    CHEM = "chem"
    PSEUDO = "pseudo"
    CERTIFICATION = "certification"


# Module-level lookup so normalize_fiber() does not rebuild the dict each call.
_FIBER_BY_NAME: dict[str, Fiber] = {f.value.lower(): f for f in Fiber}


@dataclass(frozen=True)
class FiberMeta:
    """Convenience wrapper for a bundle's fiber metadata."""

    fiber: Fiber
    domains: tuple[str, ...]

    def to_relations(self, asset_cid: str) -> list[Relation]:
        return fiber_relations(asset_cid, self.fiber.value, self.domains)


def normalize_fiber(name: str | Fiber) -> Fiber:
    """Return the canonical Fiber enum member for a loose name.

    Raises ``ValueError`` for unknown fibers so typos fail fast.
    """
    if isinstance(name, Fiber):
        return name
    key = name.strip().lower()
    try:
        return _FIBER_BY_NAME[key]
    except KeyError as exc:
        raise ValueError(f"unknown fiber: {name!r}") from exc


def normalize_domain(name: str) -> str:
    """Normalise a domain tag: lower-case, hyphenated, no extra whitespace."""
    return "-".join(part for part in name.strip().lower().split() if part)


def fiber_relations(
    asset_cid: str,
    fiber: str | Fiber,
    domains: Iterable[str] | None = None,
) -> list[Relation]:
    """Build relations that declare a bundle's fiber and domains.

    These relations ride inside ordinary bytecode, so the format needs no
    version bump.  They are typically the first relations ingested so the Lens
    can tag all downstream atoms with provenance.
    """
    fiber_value = normalize_fiber(fiber).value
    relations = [
        Relation(
            subject=asset_cid,
            predicate=FIBER_PREDICATE,
            obj=fiber_value,
            source_type="Dataset",
            weight=1,
        ),
    ]
    for domain in domains or ():
        normalized = normalize_domain(domain)
        if normalized:
            relations.append(
                Relation(
                    subject=asset_cid,
                    predicate=DOMAIN_PREDICATE,
                    obj=normalized,
                    source_type="Dataset",
                    weight=1,
                )
            )
    return relations
