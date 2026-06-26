"""Settle a governed instrument decision as an integer Knit — the Knitweb half of the seam.

The float analytics layer (the standalone VoteBank repo) values instruments and *decides* what to
pay: a bond coupon, a redemption at face, or a conversion into underlying units. It values those in
real numbers, then **quantises** the decision to an integer base-unit amount and hands it across
the seam. This module is the Knitweb half — it takes an integer-only :class:`SettlementOrder` and
executes it as a dual-signed :class:`~knitweb.ledger.knit.Knit` on the value-path.

The seam is strictly one-way and Knitweb **never imports the float layer**: an order arrives as
plain integers (the amount is already quantised on the far side), so no float ever reaches
canonical encoding. ``SettlementOrder`` re-asserts integer-ness at the boundary — a float amount is
a programming error and is rejected before it can touch a signed record.

  * ``COUPON`` / ``REDEMPTION`` settle native ``PLS`` from issuer → holder.
  * ``CONVERSION`` settles ``amount`` base units of the *underlying* ``symbol`` (the holder takes
    tokens instead of cash redemption).

All three are issuer → holder transfers, so :func:`settle` is uniform: it reuses
:meth:`~knitweb.ledger.node.AccountNode.transfer_to` (propose → accept → validate → apply), which
already enforces nonce/anti-replay, the network id, and a sufficient balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core import canonical
from ..ledger.knit import Knit
from ..ledger.node import AccountNode
from ..token.mint import NATIVE

__all__ = ["SettlementKind", "SettlementOrder", "settle"]


class SettlementKind(str, Enum):
    """What a settlement order asks the value-path to settle (mirrors the analytics layer)."""

    COUPON = "COUPON"          # periodic coupon payment (native PLS)
    REDEMPTION = "REDEMPTION"  # face value at maturity (native PLS)
    CONVERSION = "CONVERSION"  # maturity taken as underlying-token units (not PLS)


@dataclass(frozen=True)
class SettlementOrder:
    """An integer settlement order received from the float analytics layer.

    The ``amount`` is already quantised to base units on the far side of the seam — PLS-wei for a
    ``COUPON`` / ``REDEMPTION``, or underlying-token base units for a ``CONVERSION``. Integer-only
    by construction: the float → integer crossing happened off the value-path, and this record
    refuses to carry a float onto it.
    """

    kind: SettlementKind
    amount: int
    symbol: str = NATIVE
    beat: int = 0
    ref: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SettlementKind):
            raise TypeError("kind must be a SettlementKind")
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise TypeError("amount must be int — the float→int crossing happens off the value-path")
        if self.amount < 0:
            raise ValueError("amount must be >= 0")
        if not isinstance(self.beat, int) or isinstance(self.beat, bool):
            raise TypeError("beat must be int")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")

    def to_record(self) -> dict:
        """The auditable, content-addressable record of this order (float-free)."""
        return {
            "kind": "govern-settlement",
            "settle_kind": self.kind.value,
            "amount": self.amount,
            "symbol": self.symbol,
            "beat": self.beat,
            "ref": self.ref,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())


def settle(
    order: SettlementOrder,
    issuer: AccountNode,
    holder: AccountNode,
    *,
    timestamp: int,
) -> Knit:
    """Execute an integer settlement order as a dual-signed Knit from ``issuer`` to ``holder``.

    Reuses :meth:`AccountNode.transfer_to`, so the Knit is proposed, both-signed, validated
    (nonce, network id, sufficient balance), and applied to both braids. Returns the settled Knit.
    Raises ``ValueError`` if the issuer cannot cover the amount (the value-path's own guard).
    """
    return issuer.transfer_to(holder, order.symbol, order.amount, timestamp)
