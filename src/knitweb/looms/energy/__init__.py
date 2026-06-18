"""Energy-balance loom — emit signed, energy-conserving grid dispatch events into the Web.

Over any settlement interval energy is conserved: what is generated, plus what storage
discharges, equals what is consumed, lost, and charged into storage. Writing the net storage
change as a signed integer ``storage_delta`` (**+charge / −discharge**), the loom's soundness
gate is a single integer identity:

    Σ generation  ==  Σ consumption + Σ losses + storage_delta

An event that violates it describes energy created or destroyed from nothing — physically
impossible — and is refused before any signature is produced. This is the exact same discipline
as the chemistry loom's element/charge balance and the supply-chain loom's mass balance,
generalised to power: a peer can trust a signed record's *shape* and re-check its *soundness*
deterministically.

Everything on the signed path is integer watt-hours, so the record round-trips through
canonical CBOR; a balanced event becomes a signed, content-addressed ``energy-balance`` record
woven into the Web by its actor. (Flows carry only positive watt-hours; the one signed quantity
that may be negative is ``storage_delta`` — a discharging battery legitimately lets generation
fall below load.)
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web

__all__ = [
    "Flow",
    "DispatchEvent",
    "EnergyLoom",
    "energy_balance",
    "is_conserved",
]


@dataclass(frozen=True)
class Flow:
    """``wh`` watt-hours on one named ``channel`` (a generator, a load, or a loss path)."""

    channel: str
    wh: int

    def __post_init__(self) -> None:
        if not isinstance(self.wh, int) or isinstance(self.wh, bool):
            raise TypeError("flow wh must be int")
        if self.wh <= 0:
            raise ValueError(f"{self.channel}: wh must be a positive integer")


@dataclass(frozen=True)
class DispatchEvent:
    """Energy over an interval: ``generation`` supplied, ``consumption`` served, ``losses``
    dissipated, and a net ``storage_delta`` into storage (+charge / −discharge), by ``actor``.
    """

    generation: tuple[Flow, ...]
    consumption: tuple[Flow, ...]
    losses: tuple[Flow, ...]
    storage_delta: int
    actor: str   # PLS address of the operator emitting the dispatch

    def __post_init__(self) -> None:
        if not isinstance(self.storage_delta, int) or isinstance(self.storage_delta, bool):
            raise TypeError("storage_delta must be int")
        if not self.generation and not self.consumption:
            raise ValueError("a dispatch needs at least one generation or consumption flow")


def _total(flows: tuple[Flow, ...]) -> int:
    return sum(flow.wh for flow in flows)


def energy_balance(event: DispatchEvent) -> int:
    """Net watt-hours: ``generation − consumption − losses − storage_delta``. Conserved ⇔ 0."""
    return (
        _total(event.generation)
        - _total(event.consumption)
        - _total(event.losses)
        - event.storage_delta
    )


def is_conserved(event: DispatchEvent) -> bool:
    """True iff generation equals consumption + losses + net storage change."""
    return energy_balance(event) == 0


def _sorted_flows(flows: tuple[Flow, ...]) -> list[Flow]:
    """Canonical flow order (by channel) so the same event in any order shares one CID."""
    return sorted(flows, key=lambda flow: (flow.channel, flow.wh))


class EnergyLoom:
    """Emits signed, energy-conserved grid dispatch events for one operator key."""

    KIND = "energy-balance"

    def __init__(self, actor_priv: str) -> None:
        self._priv = actor_priv
        self.actor_pub = crypto.public_from_private(actor_priv)
        self.address = crypto.address(self.actor_pub)

    def to_record(self, event: DispatchEvent) -> dict:
        if event.actor != self.address:
            raise ValueError("dispatch actor does not match signing key")

        def flow_rec(flow: Flow) -> dict:
            return {"channel": flow.channel, "wh": flow.wh}

        record = {
            "kind": self.KIND,
            "generation": [flow_rec(f) for f in _sorted_flows(event.generation)],
            "consumption": [flow_rec(f) for f in _sorted_flows(event.consumption)],
            "losses": [flow_rec(f) for f in _sorted_flows(event.losses)],
            "storage_delta": event.storage_delta,
            "actor": self.address,
            "total_generation_wh": _total(event.generation),
            "conserved": True,
        }
        canonical.encode(record)  # fail fast on any non-canonical content
        return record

    def emit(self, event: DispatchEvent) -> Attestation:
        """Validate energy conservation, then sign the event. Raises if not conserved."""
        net = energy_balance(event)
        if net != 0:
            raise ValueError(f"energy not conserved (net {net} Wh), cannot sign")
        return attest(self.to_record(event), self._priv, author_field="actor")

    def weave(self, event: DispatchEvent, web: Web) -> tuple[str, Attestation]:
        """Emit a signed event and weave it into *web*; return (cid, attestation)."""
        att = self.emit(event)
        return web.weave(att.record), att
