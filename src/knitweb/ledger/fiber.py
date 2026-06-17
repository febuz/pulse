"""Fiber — a content-addressed, hash-chained account commitment.

A Fiber is the value-unit of Knitweb (Dutch *vezel*): an immutable, content-
addressed snapshot of one account's Blob state at a given sequence number, linked
to the previous Fiber. A node's Fibers form a chain (its Braid). Each non-genesis
Fiber records the Knit that caused the transition, so history is fully auditable.

Fields are integers/strings only (canonical-encoding friendly). The Fiber's CID
is the content hash over its canonical record.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical
from . import blob

__all__ = ["Fiber", "genesis_fiber"]


@dataclass(frozen=True)
class Fiber:
    """An immutable account-state commitment at sequence ``seq``."""

    owner: str                  # owner public-key hex (compressed)
    seq: int                    # 0 for genesis, strictly increasing
    balances: dict[str, int]    # integer balances after this Fiber
    nonce: int                  # account nonce after this Fiber
    prev: str | None            # CID of the previous Fiber, or None at genesis
    knit: str | None            # CID of the causing Knit, or None at genesis

    def to_record(self) -> dict:
        return {
            "kind": "fiber",
            "owner": self.owner,
            "seq": self.seq,
            "balances": blob.normalize(self.balances),
            "nonce": self.nonce,
            "prev": self.prev,
            "knit": self.knit,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())

    def balance(self, symbol: str) -> int:
        return blob.balance_of(self.balances, symbol)


def genesis_fiber(owner: str, balances: dict[str, int] | None = None) -> Fiber:
    """Create the seq-0 Fiber for ``owner``.

    For the credibly-neutral PLS base layer, genesis balances are empty
    (``premine=0``). A non-empty ``balances`` is permitted only for test fixtures
    and for explicit, transparent allocations (never for the native PLS premine).
    """
    return Fiber(
        owner=owner,
        seq=0,
        balances=blob.normalize(balances or {}),
        nonce=0,
        prev=None,
        knit=None,
    )
