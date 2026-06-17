"""Knit — a two-party, dual-signed value transfer (one of the seven primitives).

A Knit moves an integer ``amount`` of a token ``symbol`` from a sender to a
receiver. It is the only way value moves in Knitweb. Both parties sign the same
canonical record, so a transfer is mutually agreed and non-repudiable. The
sender's ``from_nonce`` pins the transfer to a specific account state, preventing
replay/double-spend.

A Knit is *settlement data*, not application logic: it carries no floats and no
free-form fields beyond what the Loom validates.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..core import canonical, crypto

__all__ = ["Knit", "build", "sign_from", "sign_to"]


@dataclass(frozen=True)
class Knit:
    from_pub: str
    to_pub: str
    symbol: str
    amount: int
    from_nonce: int
    timestamp: int
    from_sig: str | None = None
    to_sig: str | None = None

    def to_record(self) -> dict:
        """The signed payload — signatures are NOT part of the signed bytes."""
        return {
            "kind": "knit",
            "from": self.from_pub,
            "to": self.to_pub,
            "symbol": self.symbol,
            "amount": self.amount,
            "from_nonce": self.from_nonce,
            "timestamp": self.timestamp,
        }

    @property
    def signing_bytes(self) -> bytes:
        return canonical.encode(self.to_record())

    @property
    def id(self) -> str:
        """Content id over the signed record (excludes signatures)."""
        return canonical.cid(self.to_record())


def build(
    from_pub: str,
    to_pub: str,
    symbol: str,
    amount: int,
    from_nonce: int,
    timestamp: int,
) -> Knit:
    """Construct an unsigned Knit."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError("amount must be int")
    return Knit(
        from_pub=from_pub,
        to_pub=to_pub,
        symbol=symbol,
        amount=amount,
        from_nonce=from_nonce,
        timestamp=timestamp,
    )


def sign_from(knit: Knit, from_priv: str) -> Knit:
    """Attach the sender's signature over the canonical record."""
    sig = crypto.sign(from_priv, knit.signing_bytes)
    return replace(knit, from_sig=sig)


def sign_to(knit: Knit, to_priv: str) -> Knit:
    """Attach the receiver's signature over the canonical record."""
    sig = crypto.sign(to_priv, knit.signing_bytes)
    return replace(knit, to_sig=sig)
