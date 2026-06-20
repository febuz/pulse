"""Co-signed personhood anchor — verifier RP *and* holder pairwise key both sign.

A ``personhood-anchor`` makes two distinct claims that need two distinct signers:

  * the **verifier** (an eIDAS Relying Party node) attests "I checked a valid, unique
    EU natural person via an accepted issuer", and
  * the **holder** attests, with their per-scope *pairwise* key, "I consent to this
    anchor binding my scope identity" — proving a human was in the loop.

Requiring both from day one is irreversible by design: a single-signed anchor could
never be upgraded to prove holder consent without re-anchoring everyone. Both
signatures are kept *outside* the record (each is a :class:`fabric.attest.Attestation`
over the same canonical bytes), so the anchor's CID stays a pure content hash.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical, crypto
from ..fabric.attest import Attestation, attest
from ..fabric.web import Web
from . import records
from .verifier import Admission

__all__ = ["CoSignedAnchor", "co_sign_anchor", "anchor_from_admission"]


@dataclass(frozen=True)
class CoSignedAnchor:
    """A ``personhood-anchor`` record with both the verifier and holder signatures."""

    record: dict
    verifier_att: Attestation   # author_field="verifier"
    holder_att: Attestation     # author_field="holder_pairwise"

    @property
    def cid(self) -> str:
        """Content id of the record (signatures are not part of the identity)."""
        return canonical.cid(self.record)

    def verify(self) -> bool:
        """True iff two *distinct* keys validly signed the *same* record."""
        if self.verifier_att.record != self.record:
            return False
        if self.holder_att.record != self.record:
            return False
        # Defense-in-depth alongside the schema check: the verifier and holder must be
        # two different keys (a single self-co-signed key would not prove holder consent).
        if self.verifier_att.author_pub == self.holder_att.author_pub:
            return False
        return self.verifier_att.verify("verifier") and self.holder_att.verify(
            "holder_pairwise"
        )

    def weave(self, web: Web) -> str:
        """Weave the (already co-signed, shape-checked) anchor into ``web``; return CID."""
        return web.weave(self.record)


def co_sign_anchor(
    record: dict,
    verifier_priv: str,
    holder_pairwise_priv: str,
) -> CoSignedAnchor:
    """Validate the anchor shape, then co-sign it with the verifier and holder keys.

    ``fabric.attest.attest`` enforces that ``record['verifier']`` and
    ``record['holder_pairwise']`` each derive from the supplied signing key, so a
    party can only co-sign an anchor it actually claims.
    """
    records.assert_personhood_record_shape(record, kind=records.ANCHOR_KIND)
    verifier_att = attest(record, verifier_priv, author_field="verifier")
    holder_att = attest(record, holder_pairwise_priv, author_field="holder_pairwise")
    return CoSignedAnchor(
        record=record, verifier_att=verifier_att, holder_att=holder_att
    )


def anchor_from_admission(
    admission: Admission,
    verifier_priv: str,
    holder_pairwise_priv: str,
    *,
    revocation_pointer: str,
) -> CoSignedAnchor:
    """Build and co-sign a personhood-anchor from a verified :class:`Admission`.

    ``revocation_pointer`` is a fresh **random** 32-byte-hex commitment (generate it with
    ``crypto.sha256(os.urandom(32)).hex()`` or equivalent). It is deliberately decoupled
    from the nullifier: the RP keeps the (pointer ↔ anchor) mapping off-fabric so it can
    revoke later, but the pointer reveals nothing about the person.
    """
    record = records.build_anchor_record(
        verifier=crypto.address(crypto.public_from_private(verifier_priv)),
        holder_pairwise=admission.holder_pairwise,
        issuer_trust_anchor=admission.issuer_trust_anchor,
        issuer_class=admission.issuer_class,
        scope=admission.scope,
        scope_nullifier=admission.scope_nullifier,
        not_before=admission.not_before,
        not_after=admission.not_after,
        revocation_pointer=revocation_pointer,
        proof_digest=admission.proof_digest,
        nullifier_scheme=admission.nullifier_scheme,
        key_scheme=admission.key_scheme,
        pairwise_did=admission.pairwise_did,
    )
    return co_sign_anchor(record, verifier_priv, holder_pairwise_priv)
