"""Braid — a node's local, append-only history chain (one of the seven primitives).

A Braid is the ordered chain of one account's Fibers. It enforces local integrity
independent of the network:

  * sequence numbers increase by exactly one,
  * each Fiber links to the previous Fiber's CID,
  * a given Knit is applied at most once (the spent-input / double-spend guard),
  * the account nonce never decreases.

Because each node can verify its own Braid from signed Fibers alone, double-spends
are caught locally before they ever propagate.
"""

from __future__ import annotations

from .fiber import Fiber

__all__ = ["Braid", "BraidError"]


class BraidError(ValueError):
    """Raised when a Fiber would violate Braid integrity."""


class Braid:
    """An append-only chain of an account's Fibers, with a spent-Knit guard."""

    def __init__(self, genesis: Fiber) -> None:
        if genesis.seq != 0 or genesis.prev is not None or genesis.knit is not None:
            raise BraidError("genesis fiber must have seq=0, prev=None, knit=None")
        self.owner = genesis.owner
        self.fibers: list[Fiber] = [genesis]
        self._spent_knits: set[str] = set()

    @property
    def head(self) -> Fiber:
        return self.fibers[-1]

    def weave(self, fiber: Fiber) -> Fiber:
        """Append ``fiber`` to the chain after checking all Braid invariants."""
        head = self.head
        if fiber.owner != self.owner:
            raise BraidError("fiber owner does not match braid owner")
        if fiber.seq != head.seq + 1:
            raise BraidError(f"seq must be {head.seq + 1}, got {fiber.seq}")
        if fiber.prev != head.cid:
            raise BraidError("fiber does not link to current head")
        if fiber.nonce < head.nonce:
            raise BraidError("nonce decreased")
        if fiber.knit is not None and fiber.knit in self._spent_knits:
            raise BraidError(f"knit already applied (double-spend): {fiber.knit}")

        self.fibers.append(fiber)
        if fiber.knit is not None:
            self._spent_knits.add(fiber.knit)
        return fiber

    def validate(self) -> bool:
        """Re-verify the entire chain from genesis (auditable by any peer)."""
        seen: set[str] = set()
        for i, fiber in enumerate(self.fibers):
            if fiber.owner != self.owner:
                return False
            if i == 0:
                if fiber.seq != 0 or fiber.prev is not None:
                    return False
            else:
                prev = self.fibers[i - 1]
                if fiber.seq != prev.seq + 1:
                    return False
                if fiber.prev != prev.cid:
                    return False
                if fiber.nonce < prev.nonce:
                    return False
                if fiber.knit is not None:
                    if fiber.knit in seen:
                        return False
                    seen.add(fiber.knit)
        return True
