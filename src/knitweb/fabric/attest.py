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

__all__ = [
    "Attestation",
    "attest",
    "verify_record",
    "RecordCheck",
    "check_record",
    "node_is_attested",
]


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
    the record's author/provider address matches that key.

    Defensive: returns ``False`` (never raises) on a non-dict record or a malformed
    ``author_pub`` (e.g. non-hex / wrong length), so callers using it in a boolean/audit
    context — including every ``audit_*`` over attacker-supplied wire envelopes — get a clean
    reject instead of a ValueError. (``crypto.verify`` already swallows a bad signature hex.)"""
    if not isinstance(record, dict):
        return False
    try:
        expected = crypto.address(author_pub)
        message = canonical.encode(record)
    except (ValueError, TypeError):
        return False
    if record.get(author_field) != expected:
        return False
    return crypto.verify(author_pub, message, sig)


@dataclass(frozen=True)
class RecordCheck:
    """Structured verdict from :func:`check_record` — a Lens-facing record audit.

    ``ok`` is the verdict; ``reason`` names the first gate that failed (or
    ``"ok"``), so an external interpret/retrieval tool can tell *why* a record was
    rejected — a tampered CID vs. a wrong author key vs. a bad signature vs. a
    non-canonical/float field — instead of getting an opaque boolean. ``RecordCheck``
    is falsy when ``ok`` is False, so ``if check_record(...):`` reads naturally.
    """

    ok: bool
    reason: str

    def __bool__(self) -> bool:
        return self.ok


def check_record(
    record: dict,
    expected_cid: str,
    author_pub: str,
    sig: str,
    *,
    author_field: str = "author",
) -> RecordCheck:
    """Full read-only audit of one signed, content-addressed record, for external
    (Lens / interpret / retrieval) tools.

    A consumer reading a record off the wire needs four independent guarantees;
    this composes them in order, each with a distinct failure ``reason``:

      1. ``record-not-a-dict``    — the record is not a CBOR map.
      2. ``non-canonical-record`` — it does not float-free canonically encode (a
         float or otherwise unencodable value ⇒ its bytes, CID and signature are
         undefined, so nothing downstream can be trusted).
      3. ``cid-mismatch``         — ``expected_cid`` ≠ the CIDv1 recomputed from
         the record (tamper-evidence: the bytes are not what the CID addresses).
      4. ``bad-author-pub`` / ``author-mismatch`` — ``author_pub`` is malformed,
         or the record's ``author_field`` address is not the one it derives.
      5. ``bad-signature``        — ``sig`` is not a valid ECDSA signature over the
         record's canonical bytes by ``author_pub``.

    Never raises on hostile input — a malformed record, key, CID or signature is
    reported as ``RecordCheck(False, reason)``, mirroring :func:`verify_record`.
    Together with the L0 ``canonical`` and ``crypto`` primitives this is the stable
    boundary Lens should depend on; Lens must not reach into Pulse internals
    (knitweb/pulse#153, #154).
    """
    if not isinstance(record, dict):
        return RecordCheck(False, "record-not-a-dict")
    # (2) float-free canonical encoding — also yields the exact bytes we hash/verify over.
    try:
        message = canonical.encode(record)
    except canonical.CanonicalError:
        return RecordCheck(False, "non-canonical-record")
    # (3) CID recomputation: the record must hash to the CID it is addressed by.
    if canonical.cid(record) != expected_cid:
        return RecordCheck(False, "cid-mismatch")
    # (4) the author/provider field must bind to the presented key.
    try:
        expected = crypto.address(author_pub)
    except (ValueError, TypeError):
        return RecordCheck(False, "bad-author-pub")
    if record.get(author_field) != expected:
        return RecordCheck(False, "author-mismatch")
    # (5) the signature must verify over the canonical bytes.
    if not crypto.verify(author_pub, message, sig):
        return RecordCheck(False, "bad-signature")
    return RecordCheck(True, "ok")


def node_is_attested(record_source: dict | object, node_cid: str) -> bool:
    """Best-effort attestability check for a web node CID.

    The core web currently stores mixed node kinds; some are plain content records,
    some are explicitly attested via an ``attestation`` envelope. For the distiller's
    guard this helper returns ``True`` when either:

    1. the CID resolves to a record carrying a valid attestation envelope, or
    2. the record is present and cycle-safe from a provenance perspective
       (non-fabricated historical data in the current web).

    The second branch avoids breaking existing signed/unsigned legacy records while
    still preventing obviously fabricated references.
    """
    from . import provenance
    from .web import Web

    # ``record_source`` is intentionally typed wide to allow callers to pass either
    # a full Web object (common in distill) or a single record map in tests.
    if isinstance(record_source, Web):
        record = record_source.get(node_cid)
    elif isinstance(record_source, dict):
        record = record_source
    else:
        return False

    if record is None or not isinstance(record, dict):
        return False

    # Explicit attestation form: {"attestation":{"author":"...", "sig":"..."}}
    att = record.get("attestation")
    if isinstance(att, dict):
        sig = att.get("sig")
        author = att.get("author")
        author_field = att.get("author_field", "author")
        if isinstance(sig, str) and isinstance(author, str):
            if verify_record(record.get("record", record), author, sig, author_field=author_field):
                return True

    # Legacy fallback: accept the node if it is present and provenance is acyclic.
    if isinstance(record_source, Web):
        try:
            return provenance.is_acyclic(record_source, node_cid)
        except Exception:
            return False
    return True
