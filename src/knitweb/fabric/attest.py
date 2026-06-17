"""Attestation — bind a fabric item to its author with an ECDSA signature.

Phase 2 items (KnowledgeItem, ResourceItem, …) are *content-addressed* but, on
their own, not *attributable*: anyone could publish a record carrying someone
else's PLS address. KnitNet principle 6 ("every claim is signed by its author")
requires authorship to be cryptographically verifiable, so the fabric can
validate-at-read instead of trusting an address string.

An :class:`Attestation` wraps a record's canonical bytes with the author's public
key and an ECDSA signature. The signature is kept *outside* the record, so the
item's CID stays a pure content hash; attribution is a separate, verifiable
envelope. ``verify`` confirms two things:

  1. the record's author/provider address derives from ``author_pub``, and
  2. the signature is valid over the record's canonical bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical, crypto

__all__ = ["Attestation", "attest", "verify_record"]


@dataclass(frozen=True)
class Attestation:
    """A signed envelope over a fabric item record."""

    record: dict
    author_pub: str   # compressed secp256k1 public key (hex)
    sig: str          # DER signature (hex) over canonical.encode(record)

    @property
    def cid(self) -> str:
        """Content id of the *record* (signature is not part of the identity)."""
        return canonical.cid(self.record)

    def verify(self, author_field: str = "author") -> bool:
        """True iff the record's author address matches the key and the sig checks."""
        return verify_record(self.record, self.author_pub, self.sig, author_field)


def attest(record: dict, author_priv: str, author_field: str = "author") -> Attestation:
    """Sign ``record`` with ``author_priv``.

    The record's ``author_field`` (e.g. "author" for KnowledgeItem, "provider" for
    ResourceItem) must already equal the address derived from the signing key, so
    a spider can only attest records it actually claims.
    """
    author_pub = crypto.public_from_private(author_priv)
    expected = crypto.address(author_pub)
    claimed = record.get(author_field)
    if claimed != expected:
        raise ValueError(
            f"record {author_field}={claimed!r} does not match signing key "
            f"address {expected!r}"
        )
    sig = crypto.sign(author_priv, canonical.encode(record))
    return Attestation(record=record, author_pub=author_pub, sig=sig)


def verify_record(
    record: dict,
    author_pub: str,
    sig: str,
    author_field: str = "author",
) -> bool:
    """Verify that ``record`` was signed by the holder of ``author_pub`` and that
    the record's author/provider address matches that key."""
    claimed = record.get(author_field)
    if claimed != crypto.address(author_pub):
        return False
    return crypto.verify(author_pub, canonical.encode(record), sig)
