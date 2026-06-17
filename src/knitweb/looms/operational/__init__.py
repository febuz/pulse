"""Operational loom — signed capacity allocations for the compute-resource market.

Where the chemistry and supply-chain looms gate on an *equality* (mass/element
conservation), the operational loom gates on an *inequality*: a provider may never
allocate more of a resource than it actually has. An :class:`AllocationEvent`
publishes how a provider splits a bounded ``capacity`` (e.g. 24 GPU units) into
consumer ``leases``; the soundness gate is **no over-allocation** —
``sum(lease units) <= capacity`` — so a provider cannot sell capacity it does not
own. Over-allocation is refused before any signature is produced.

Prices are integer PLS-wei per Pulse epoch; everything on the signed path is integer
so it round-trips through canonical CBOR. A valid allocation becomes a signed,
content-addressed ``capacity-allocation`` record woven into the Web by its provider —
the market side of the DePIN fabric (consumers later pay these leases via PoUW escrow).
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web

__all__ = [
    "Resource",
    "Lease",
    "AllocationEvent",
    "OperationalLoom",
    "allocated_units",
    "idle_units",
    "is_within_capacity",
]


@dataclass(frozen=True)
class Resource:
    """A bounded resource a provider offers (e.g. kind="gpu-3090", capacity=24)."""

    kind: str
    capacity: int

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("resource capacity must be a positive integer")


@dataclass(frozen=True)
class Lease:
    """``units`` of the resource leased to ``consumer`` at an integer epoch price."""

    consumer: str          # PLS address of the leasing consumer
    units: int
    price_per_epoch: int   # PLS-wei per Pulse epoch

    def __post_init__(self) -> None:
        if self.units <= 0:
            raise ValueError("lease units must be a positive integer")
        if self.price_per_epoch < 0:
            raise ValueError("price_per_epoch must be non-negative")


@dataclass(frozen=True)
class AllocationEvent:
    """A provider's split of one resource's capacity into consumer leases."""

    resource: Resource
    leases: tuple[Lease, ...]
    provider: str          # PLS address of the offering provider

    def __post_init__(self) -> None:
        if not self.leases:
            raise ValueError("an allocation needs at least one lease")


def allocated_units(event: AllocationEvent) -> int:
    """Total resource units committed across all leases."""
    return sum(lease.units for lease in event.leases)


def idle_units(event: AllocationEvent) -> int:
    """Unallocated capacity (>= 0 when the allocation is sound)."""
    return event.resource.capacity - allocated_units(event)


def is_within_capacity(event: AllocationEvent) -> bool:
    """True iff the provider has not over-allocated its capacity."""
    return allocated_units(event) <= event.resource.capacity


def _sorted_leases(leases: tuple[Lease, ...]) -> list[Lease]:
    """Canonical lease order so the same allocation in any order shares one CID."""
    return sorted(leases, key=lambda lease: (lease.consumer, lease.units, lease.price_per_epoch))


class OperationalLoom:
    """Emits signed, over-allocation-checked capacity allocations for one provider key."""

    KIND = "capacity-allocation"

    def __init__(self, provider_priv: str) -> None:
        self._priv = provider_priv
        self.provider_pub = crypto.public_from_private(provider_priv)
        self.address = crypto.address(self.provider_pub)

    def to_record(self, event: AllocationEvent) -> dict:
        def lease_rec(lease: Lease) -> dict:
            return {
                "consumer": lease.consumer,
                "units": lease.units,
                "price_per_epoch": lease.price_per_epoch,
            }
        record = {
            "kind": self.KIND,
            "resource_kind": event.resource.kind,
            "capacity": event.resource.capacity,
            "leases": [lease_rec(lease) for lease in _sorted_leases(event.leases)],
            "allocated": allocated_units(event),
            "idle": idle_units(event),
            "provider": self.address,
        }
        canonical.encode(record)  # fail fast on any non-canonical content
        return record

    def emit(self, event: AllocationEvent) -> Attestation:
        """Validate no-over-allocation, then sign the allocation. Raises if oversold."""
        if not is_within_capacity(event):
            raise ValueError(
                f"over-allocation: {allocated_units(event)} units > capacity "
                f"{event.resource.capacity}, cannot sign"
            )
        return attest(self.to_record(event), self._priv, author_field="provider")

    def weave(self, event: AllocationEvent, web: Web) -> tuple[str, Attestation]:
        """Emit a signed allocation and weave it into *web*; return (cid, attestation)."""
        att = self.emit(event)
        return web.weave(att.record), att
