"""AccountNode — a single account that owns a keypair, a Blob, and a Braid.

This ties the ledger primitives together for local (offline) settlement. A node
proposes and signs Knits as a sender, accepts and signs them as a receiver, and
applies them to its own Braid. There is no peer-to-peer wiring here — that arrives
in the P2P layer (L2); the value of this layer is that the economic rules are
provable in isolation.
"""

from __future__ import annotations

from ..core import crypto
from . import loom
from .braid import Braid
from .fiber import genesis_fiber
from .knit import MAINNET, Knit, build, sign_from, sign_to

__all__ = ["AccountNode"]


class AccountNode:
    def __init__(
        self,
        priv: str | None = None,
        pub: str | None = None,
        genesis_balances: dict[str, int] | None = None,
        network: int = MAINNET,
    ) -> None:
        if priv is None or pub is None:
            priv, pub = crypto.generate_keypair()
        self.priv = priv
        self.pub = pub
        self.network = network
        self.address = crypto.address(pub)
        self.braid = Braid(genesis_fiber(pub, genesis_balances))

    @classmethod
    def from_seed(
        cls,
        seed: str,
        genesis_balances: dict[str, int] | None = None,
        network: int = MAINNET,
    ) -> "AccountNode":
        """A **deterministic** account derived from an arbitrary external seed/id.

        The same ``seed`` always yields the same account (key + address), so an app can
        bridge an external identity (a wallet id, a username, a device id) to a stable
        knitweb account across sessions and machines — without storing a key. The seed is
        domain-separated and SHA-256'd into a secp256k1 private scalar.

        ``genesis_balances`` is dev/test seeding only (the native PLS layer has no premine).
        """
        import hashlib

        priv = hashlib.sha256(f"knitweb:account:seed:{seed}".encode()).hexdigest()
        return cls(priv=priv, pub=crypto.public_from_private(priv),
                   genesis_balances=genesis_balances, network=network)

    # -- views -------------------------------------------------------------

    @property
    def nonce(self) -> int:
        return self.braid.head.nonce

    def balance(self, symbol: str = "PLS") -> int:
        return self.braid.head.balance(symbol)

    # -- the two-party transfer handshake ---------------------------------

    def propose(self, to_pub: str, symbol: str, amount: int, timestamp: int) -> Knit:
        """Build and sender-sign a Knit using this node's current nonce + network."""
        knit = build(self.pub, to_pub, symbol, amount, self.nonce, timestamp,
                     network=self.network)
        return sign_from(knit, self.priv)

    def accept(self, knit: Knit) -> Knit:
        """Receiver-sign a proposed Knit (must be addressed to this node)."""
        if knit.to_pub != self.pub:
            raise ValueError("knit is not addressed to this node")
        return sign_to(knit, self.priv)

    # -- applying a fully-signed Knit -------------------------------------

    def apply_sent(self, knit: Knit) -> None:
        """Apply an outgoing Knit to this (sender) node's Braid."""
        next_fiber = loom.apply_to_sender(self.braid.head, knit, self.network)
        self.braid.weave(next_fiber)

    def apply_received(self, knit: Knit) -> None:
        """Apply an incoming Knit to this (receiver) node's Braid."""
        next_fiber = loom.apply_to_receiver(self.braid.head, knit, self.network)
        self.braid.weave(next_fiber)

    # -- convenience: full transfer between two local nodes ---------------

    def transfer_to(
        self, receiver: "AccountNode", symbol: str, amount: int, timestamp: int
    ) -> Knit:
        """Complete a transfer to ``receiver`` (both nodes local). Returns the Knit."""
        if receiver.network != self.network:
            raise ValueError(
                f"network mismatch: sender {self.network} != receiver {receiver.network}"
            )
        proposed = self.propose(receiver.pub, symbol, amount, timestamp)
        signed = receiver.accept(proposed)
        ok, reason = loom.validate_knit(signed, self.network)
        if not ok:
            raise ValueError(f"refusing to apply invalid knit: {reason}")
        self.apply_sent(signed)
        receiver.apply_received(signed)
        return signed
