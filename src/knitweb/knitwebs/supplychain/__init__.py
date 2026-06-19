"""Supply-chain knitweb — emit signed, mass-conserving transformation events into the Web.

A supply-chain process (assembly, packaging, blending, repackaging) consumes input
SKUs and produces output SKUs. Matter is not created or destroyed by such a process,
so the knitweb's soundness gate is **mass conservation**: the total mass of the inputs
must equal the total mass of the outputs (integer grams). A process that violates it
is physically impossible and is refused before any signature is produced — the exact
same discipline as the chemistry knitweb's element/charge balance, generalised to goods.

Everything on the signed path is integer-only (quantities, unit masses), so the
record round-trips through canonical CBOR; a balanced event becomes a signed,
content-addressed ``supplychain-process`` record woven into the Web by its actor.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web

__all__ = [
    "Item",
    "Line",
    "ProcessEvent",
    "SupplyChainKnitweb",
    "mass_balance",
    "is_conserved",
]


@dataclass(frozen=True)
class Item:
    """A stock-keeping unit with an integer unit mass (grams)."""

    sku: str
    unit_mass_g: int

    def __post_init__(self) -> None:
        if not isinstance(self.unit_mass_g, int) or isinstance(self.unit_mass_g, bool):
            raise TypeError("unit_mass_g must be int")
        if self.unit_mass_g <= 0:
            raise ValueError(f"{self.sku}: unit_mass_g must be a positive integer")


@dataclass(frozen=True)
class Line:
    """``qty`` units of an :class:`Item` on one side of a process."""

    item: Item
    qty: int

    def __post_init__(self) -> None:
        if not isinstance(self.qty, int) or isinstance(self.qty, bool):
            raise TypeError("line quantity must be int")
        if self.qty <= 0:
            raise ValueError("line quantity must be a positive integer")

    @property
    def mass_g(self) -> int:
        return self.item.unit_mass_g * self.qty


@dataclass(frozen=True)
class ProcessEvent:
    """A transformation: ``inputs`` consumed → ``outputs`` produced, by ``actor``."""

    inputs: tuple[Line, ...]
    outputs: tuple[Line, ...]
    actor: str   # PLS address of the actor running the process

    def __post_init__(self) -> None:
        if not self.inputs or not self.outputs:
            raise ValueError("a process needs at least one input and one output")


def mass_balance(event: ProcessEvent) -> int:
    """Net mass in grams (outputs − inputs). Conserved ⇔ 0."""
    return (
        sum(line.mass_g for line in event.outputs)
        - sum(line.mass_g for line in event.inputs)
    )


def is_conserved(event: ProcessEvent) -> bool:
    """True iff total input mass equals total output mass."""
    return mass_balance(event) == 0


def _sorted_lines(lines: tuple[Line, ...]) -> list[Line]:
    """Canonical line order (by SKU) so the same event in any order shares one CID."""
    return sorted(lines, key=lambda line: (line.item.sku, line.item.unit_mass_g, line.qty))


class SupplyChainKnitweb:
    """Emits signed, mass-conserved supply-chain process events for one actor key."""

    KIND = "supplychain-process"

    def __init__(self, actor_priv: str) -> None:
        self._priv = actor_priv
        self.actor_pub = crypto.public_from_private(actor_priv)
        self.address = crypto.address(self.actor_pub)

    def to_record(self, event: ProcessEvent) -> dict:
        if event.actor != self.address:
            raise ValueError("process actor does not match signing key")

        def line_rec(line: Line) -> dict:
            return {
                "sku": line.item.sku,
                "unit_mass_g": line.item.unit_mass_g,
                "qty": line.qty,
            }
        record = {
            "kind": self.KIND,
            "inputs": [line_rec(line) for line in _sorted_lines(event.inputs)],
            "outputs": [line_rec(line) for line in _sorted_lines(event.outputs)],
            "actor": self.address,
            "total_mass_g": sum(line.mass_g for line in event.inputs),
            "conserved": True,
        }
        canonical.encode(record)  # fail fast on any non-canonical content
        return record

    def emit(self, event: ProcessEvent) -> Attestation:
        """Validate mass conservation, then sign the event. Raises if not conserved."""
        net = mass_balance(event)
        if net != 0:
            raise ValueError(f"mass not conserved (net {net} g), cannot sign")
        return attest(self.to_record(event), self._priv, author_field="actor")

    def weave(self, event: ProcessEvent, web: Web) -> tuple[str, Attestation]:
        """Emit a signed event and weave it into *web*; return (cid, attestation)."""
        att = self.emit(event)
        return web.weave(att.record), att
