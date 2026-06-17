"""Operational loom — emit signed, capacity-feasible allocation events into the Web.

An operational loom models resource capacity scheduling: given named resource pools
with integer capacity limits, an allocation event assigns integer units to one or
more tasks. The soundness gate is **feasibility** — no resource is over-subscribed:

    sum(units for all claims on resource R) <= R.capacity

An infeasible allocation is physically impossible (more work than available capacity)
and is refused before any signature is produced. This is the same discipline as the
chemistry loom (element/charge balance) and supply-chain loom (mass conservation),
applied to capacity scheduling.

All capacities and units are integers; records round-trip through canonical CBOR.
A feasible event becomes a signed, content-addressed ``operational-allocation``
record woven into the Web by the scheduling actor.

Separation of concerns (vs the earlier priced-lease design): this loom proves only
**capacity feasibility** — that an actor did not over-subscribe its declared pools.
It deliberately carries no pricing. Per-unit price and the provider/consumer payment
obligation live in the priced ``ResourceItem`` (``fabric/items.py``) and settle through
the finance loom + PoUW escrow. So a peer audits *"capacity was not oversubscribed"*
from this record, and *"this capacity was the priced PLS obligation"* against the
linked ResourceItem / settlement record. The binding that closes this loop lives on
the *settlement* side, not here: a finance ``LedgerEntry`` carries an optional
``settles`` set of CIDs citing the ``operational-allocation`` record and/or the
priced ``ResourceItem`` offer it pays for (see ``looms/finance``). Keeping the
reference on the finance entry — rather than baking an offer pointer into this
feasibility record — lets capacity feasibility stay a standalone, reusable proof.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web

__all__ = [
    "Resource",
    "Claim",
    "AllocationEvent",
    "OperationalLoom",
    "capacity_balance",
    "is_feasible",
]


@dataclass(frozen=True)
class Resource:
    """A named resource with an integer total capacity (e.g. GPU slots, CPU cores)."""

    name: str
    capacity: int

    def __post_init__(self) -> None:
        if not isinstance(self.capacity, int) or isinstance(self.capacity, bool):
            raise TypeError("resource capacity must be int")
        if self.capacity <= 0:
            raise ValueError(f"{self.name}: capacity must be a positive integer")


@dataclass(frozen=True)
class Claim:
    """An integer unit-demand against a named resource for a labelled task."""

    resource_name: str
    task: str
    units: int

    def __post_init__(self) -> None:
        if not isinstance(self.units, int) or isinstance(self.units, bool):
            raise TypeError("claim units must be int")
        if self.units <= 0:
            raise ValueError("claim units must be a positive integer")


@dataclass(frozen=True)
class AllocationEvent:
    """A set of claims against declared resources, scheduled by ``actor``.

    ``resources`` declares available capacity; ``claims`` express demand.
    Every resource referenced in a claim must appear in ``resources``.
    """

    resources: tuple[Resource, ...]
    claims: tuple[Claim, ...]
    actor: str  # PLS address of the scheduling spider

    def __post_init__(self) -> None:
        if not self.resources:
            raise ValueError("an allocation event needs at least one resource")
        if not self.claims:
            raise ValueError("an allocation event needs at least one claim")
        seen: set[str] = set()
        duplicates: set[str] = set()
        for resource in self.resources:
            if resource.name in seen:
                duplicates.add(resource.name)
            seen.add(resource.name)
        if duplicates:
            raise ValueError(f"duplicate resource names: {duplicates}")
        declared = {r.name for r in self.resources}
        unknown = {c.resource_name for c in self.claims} - declared
        if unknown:
            raise ValueError(f"claims reference undeclared resources: {unknown}")


# ---------------------------------------------------------------------------
# Invariant checks
# ---------------------------------------------------------------------------

def capacity_balance(event: AllocationEvent) -> dict[str, int]:
    """Per-resource remaining capacity after all claims.

    Feasible ⇔ all values >= 0. A negative value means that resource is
    over-subscribed by the absolute value of the entry.
    """
    cap = {r.name: r.capacity for r in event.resources}
    for claim in event.claims:
        cap[claim.resource_name] -= claim.units
    return cap


def is_feasible(event: AllocationEvent) -> bool:
    """True iff every resource has enough capacity to satisfy all its claims."""
    return all(v >= 0 for v in capacity_balance(event).values())


# ---------------------------------------------------------------------------
# The loom
# ---------------------------------------------------------------------------

def _sorted_resources(resources: tuple[Resource, ...]) -> list[Resource]:
    return sorted(resources, key=lambda r: r.name)


def _sorted_claims(claims: tuple[Claim, ...]) -> list[Claim]:
    return sorted(claims, key=lambda c: (c.resource_name, c.task, c.units))


class OperationalLoom:
    """Emits signed, capacity-feasible allocation events for one scheduling actor."""

    KIND = "operational-allocation"

    def __init__(self, actor_priv: str) -> None:
        self._priv = actor_priv
        self.actor_pub = crypto.public_from_private(actor_priv)
        self.address = crypto.address(self.actor_pub)

    def to_record(self, event: AllocationEvent) -> dict:
        """Build the integer-only, canonical-encodable record for an allocation event."""
        if event.actor != self.address:
            raise ValueError("allocation actor does not match signing key")
        record = {
            "kind": self.KIND,
            "resources": [
                {"name": r.name, "capacity": r.capacity}
                for r in _sorted_resources(event.resources)
            ],
            "claims": [
                {"resource": c.resource_name, "task": c.task, "units": c.units}
                for c in _sorted_claims(event.claims)
            ],
            "actor": self.address,
            "feasible": True,
        }
        canonical.encode(record)  # fail fast on any non-canonical content
        return record

    def emit(self, event: AllocationEvent) -> Attestation:
        """Validate capacity feasibility, then sign the event. Raises if over-subscribed."""
        oversubscribed = {
            name: -remaining
            for name, remaining in capacity_balance(event).items()
            if remaining < 0
        }
        if oversubscribed:
            raise ValueError(
                f"capacity exceeded, cannot sign: {oversubscribed}"
            )
        return attest(self.to_record(event), self._priv, author_field="actor")

    def weave(self, event: AllocationEvent, web: Web) -> tuple[str, Attestation]:
        """Emit a signed event and weave it into *web*; return (cid, attestation)."""
        att = self.emit(event)
        return web.weave(att.record), att
