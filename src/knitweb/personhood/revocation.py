"""Revocation log + epoch-pinned status commitment.

Revocation has two jobs here, both privacy-preserving:

  * **Audit + equivocation.** Revocations are appended to a signed ``fabric.feed`` of
    ``personhood-revoke`` records, each keyed by the anchor's *random*
    ``revocation_pointer`` — never by the nullifier — so a published revocation never
    reveals which person was revoked. The feed's signed head and ``check_conflict`` give
    the same equivocation guarantee the rest of the fabric enjoys.
  * **Race-free validity.** A vote/pledge must be checked against a *fixed* snapshot, or a
    person revoked at T could still vote at T+ε on a node that has not yet replicated the
    revocation. :class:`StatusCommitment` is the authority's signature over
    ``(scope, status_root, length, epoch)`` — the sorted :class:`StatusTree` at one epoch.
    Everyone tallies a round against the same committed root, so revocation propagation
    cannot be raced and a partitioned node cannot admit a revoked voter.

Trust model (documented): the revocation authority is trusted for *completeness* (to append
every revocation), as in any status list. The signed audit feed bounds misbehaviour — the
authority cannot present two different histories at one position without producing a
``check_conflict`` proof of equivocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..core import canonical, crypto
from ..fabric import feed as feedmod
from .records import REASON_UNSPECIFIED, build_revoke_record
from .status_tree import NonMembershipProof, StatusTree, verify_non_membership

__all__ = [
    "STATUS_NAMESPACE",
    "StatusCommitment",
    "RevocationLog",
    "check_non_revocation",
]

# Domain-separation tag: a status-commitment signature can never be replayed elsewhere.
STATUS_NAMESPACE = "knit-personhood-status:v1"


def _status_signable(scope: str, root: str, length: int, epoch: int) -> bytes:
    return canonical.encode(
        {"ns": STATUS_NAMESPACE, "scope": scope, "root": root, "length": length, "epoch": epoch}
    )


@dataclass(frozen=True)
class StatusCommitment:
    """An authority-signed commitment to the revocation status tree at one epoch."""

    scope: str
    root: str
    length: int
    epoch: int
    authority: str  # compressed secp256k1 pubkey (hex) of the revocation authority
    sig: str        # DER signature (hex) over _status_signable(...)

    def signable(self) -> bytes:
        return _status_signable(self.scope, self.root, self.length, self.epoch)

    def verify(self) -> bool:
        """True iff ``sig`` is a valid authority signature over this commitment."""
        return crypto.verify(self.authority, self.signable(), self.sig)

    @property
    def address(self) -> str:
        return crypto.address(self.authority)


class RevocationLog:
    """A signed append-only feed of revocations for one scope, plus status commitments."""

    def __init__(self, authority_priv: str, scope: str, fork: int = 0) -> None:
        if not scope:
            raise ValueError("scope must be a non-empty string")
        self._priv = authority_priv
        self.scope = scope
        self.authority = crypto.public_from_private(authority_priv)
        self.address = crypto.address(self.authority)
        self._feed = feedmod.Feed(authority_priv, fork=fork)

    @property
    def feed(self) -> feedmod.Feed:
        return self._feed

    def head(self) -> feedmod.FeedHead:
        """The current signed feed head (audit/equivocation anchor)."""
        return self._feed.head()

    def revoke(
        self,
        revocation_pointer: str,
        revoked_at: int,
        reason_code: int = REASON_UNSPECIFIED,
    ) -> feedmod.FeedHead:
        """Append a ``personhood-revoke`` (keyed by the random pointer); return the new head."""
        record = build_revoke_record(
            verifier=self.address,
            scope=self.scope,
            revocation_pointer=revocation_pointer,
            revoked_at=revoked_at,
            reason_code=reason_code,
        )
        return self._feed.append(record)

    def revoked_pointers(self) -> List[str]:
        """All revoked pointers in this scope (from the signed feed entries)."""
        return [
            e["revocation_pointer"]
            for e in self._feed.entries
            if e.get("kind") == "personhood-revoke" and e.get("scope") == self.scope
        ]

    def status_tree(self) -> StatusTree:
        return StatusTree(self.revoked_pointers())

    def commit_status(self, epoch: int) -> StatusCommitment:
        """Sign the sorted status tree at ``epoch`` (the race-free snapshot to tally against)."""
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise ValueError("epoch must be an int")
        tree = self.status_tree()
        root, length = tree.root(), tree.length
        sig = crypto.sign(self._priv, _status_signable(self.scope, root, length, epoch))
        return StatusCommitment(
            scope=self.scope, root=root, length=length, epoch=epoch,
            authority=self.authority, sig=sig,
        )

    def prove_non_revocation(
        self, revocation_pointer: str, epoch: int
    ) -> Tuple[StatusCommitment, NonMembershipProof]:
        """Return (signed status commitment, non-membership proof) for an unrevoked pointer."""
        proof = self.status_tree().prove_non_membership(revocation_pointer)
        return self.commit_status(epoch), proof


def check_non_revocation(
    commitment: StatusCommitment, proof: NonMembershipProof
) -> bool:
    """True iff ``commitment`` is authority-signed and ``proof`` shows non-revocation at it.

    The proof must be for the commitment's pointer and verify against the committed
    (root, length) — so a proof built at an earlier epoch (a stale snapshot) cannot
    satisfy a later commitment with a different root (the race-elimination property).
    """
    if not commitment.verify():
        return False
    return verify_non_membership(commitment.root, commitment.length, proof)
