"""FBRNode — a single account that owns a keypair, a Blob, and a Braid.

This ties the ledger primitives together for local (offline) settlement. A node
proposes and signs Knits as a sender, accepts and signs them as a receiver, and
applies them to its own Braid. There is no network here — that arrives in the P2P
layer (L2); the value of this layer is that the economic rules are provable in
isolation.
"""

from __future__ import annotations

from ..core import crypto
from . import loom
from .braid import Braid
from .fiber import genesis_fiber
from .knit import Knit, build, sign_from, sign_to

__all__ = ["FBRNode"]


class FBRNode:
    def __init__(
        self,
        priv: str | None = None,
        pub: str | None = None,
        genesis_balances: dict[str, int] | None = None,
    ) -> None:
        if priv is None or pub is None:
            priv, pub = crypto.generate_keypair()
        self.priv = priv
        self.pub = pub
        self.address = crypto.address(pub)
        self.braid = Braid(genesis_fiber(pub, genesis_balances))

    # -- views -------------------------------------------------------------

    @property
    def nonce(self) -> int:
        return self.braid.head.nonce

    def balance(self, symbol: str = "FBR") -> int:
        return self.braid.head.balance(symbol)

    # -- the two-party transfer handshake ---------------------------------

    def propose(self, to_pub: str, symbol: str, amount: int, timestamp: int) -> Knit:
        """Build and sender-sign a Knit using this node's current nonce."""
        knit = build(self.pub, to_pub, symbol, amount, self.nonce, timestamp)
        return sign_from(knit, self.priv)

    def accept(self, knit: Knit) -> Knit:
        """Receiver-sign a proposed Knit (must be addressed to this node)."""
        if knit.to_pub != self.pub:
            raise ValueError("knit is not addressed to this node")
        return sign_to(knit, self.priv)

    # -- applying a fully-signed Knit -------------------------------------

    def apply_sent(self, knit: Knit) -> None:
        """Apply an outgoing Knit to this (sender) node's Braid."""
        next_fiber = loom.apply_to_sender(self.braid.head, knit)
        self.braid.weave(next_fiber)

    def apply_received(self, knit: Knit) -> None:
        """Apply an incoming Knit to this (receiver) node's Braid."""
        next_fiber = loom.apply_to_receiver(self.braid.head, knit)
        self.braid.weave(next_fiber)

    # -- convenience: full transfer between two local nodes ---------------

    def transfer_to(
        self, receiver: "FBRNode", symbol: str, amount: int, timestamp: int
    ) -> Knit:
        """Complete a transfer to ``receiver`` (both nodes local). Returns the Knit."""
        proposed = self.propose(receiver.pub, symbol, amount, timestamp)
        signed = receiver.accept(proposed)
        ok, reason = loom.validate_knit(signed)
        if not ok:
            raise ValueError(f"refusing to apply invalid knit: {reason}")
        self.apply_sent(signed)
        receiver.apply_received(signed)
        return signed
