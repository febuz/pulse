"""The personhood gate — what vBank and crowdfunding call before accepting an action.

Two operations:

  * :func:`enroll` — admit a person into a scope once: verify the presentation, refuse a
    second anchor for the same scope nullifier (:class:`~knitweb.personhood.errors.AlreadyRegisteredError`),
    co-sign the anchor, and index it.
  * :func:`require_personhood` — gate an action (a vote/pledge): verify the presentation,
    require an existing anchor, check the validity window, and check **non-revocation against
    an epoch-pinned signed status commitment** (so a revocation cannot be raced). Returns a
    :class:`PersonhoodTicket`.

The ticket authorises *an action*; it is deliberately **decoupled from the action's content
signature** (the ballot/pledge is signed separately by the holder's pairwise key). That seam
is what lets receipt-freeness / a ZK content layer slot in later without changing the gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .anchor import CoSignedAnchor, anchor_from_admission
from .errors import AlreadyRegisteredError, ExpiredError, NotPersonError, RevokedError
from .revocation import RevocationLog, check_non_revocation
from .verifier import Presentation, PresentationVerifier

__all__ = ["PersonhoodTicket", "AnchorIndex", "enroll", "require_personhood"]


@dataclass(frozen=True)
class PersonhoodTicket:
    """Authorisation for one action, carrying only the (PII-free) scope identity.

    It does NOT carry or imply the content of the action — the ballot/pledge is signed
    separately by ``holder_pairwise``, so authorisation and content stay decoupled.
    """

    scope: str
    scope_nullifier: str
    pairwise_did: str
    holder_pairwise: str
    not_before: int
    not_after: int


class AnchorIndex:
    """Tracks the live anchor per (scope, scope_nullifier) — the one-person-one-scope ledger."""

    def __init__(self) -> None:
        self._by_scope: Dict[str, Dict[str, dict]] = {}

    def is_registered(self, scope: str, scope_nullifier: str) -> bool:
        return scope_nullifier in self._by_scope.get(scope, {})

    def register(self, anchor_record: dict) -> None:
        scope = anchor_record["scope"]
        nullifier = anchor_record["scope_nullifier"]
        if self.is_registered(scope, nullifier):
            raise AlreadyRegisteredError(
                "this person already holds an anchor in this scope"
            )
        self._by_scope.setdefault(scope, {})[nullifier] = anchor_record

    def anchor(self, scope: str, scope_nullifier: str) -> dict:
        return self._by_scope[scope][scope_nullifier]

    def revocation_pointer(self, scope: str, scope_nullifier: str) -> str:
        return self.anchor(scope, scope_nullifier)["revocation_pointer"]


def enroll(
    scope: str,
    presentation: Presentation,
    *,
    verifier: PresentationVerifier,
    anchor_index: AnchorIndex,
    rp_priv: str,
    holder_pairwise_priv: str,
    revocation_pointer: str,
) -> CoSignedAnchor:
    """Admit a person into ``scope`` once and index their co-signed anchor.

    Raises :class:`NotPersonError` if the presentation fails, or
    :class:`AlreadyRegisteredError` if the scope nullifier already holds an anchor.
    """
    admission = verifier.verify_presentation(scope, presentation)
    if anchor_index.is_registered(scope, admission.scope_nullifier):
        raise AlreadyRegisteredError("this person already holds an anchor in this scope")
    anchor = anchor_from_admission(
        admission, rp_priv, holder_pairwise_priv, revocation_pointer=revocation_pointer
    )
    anchor_index.register(anchor.record)
    return anchor


def require_personhood(
    scope: str,
    presentation: Presentation,
    *,
    verifier: PresentationVerifier,
    anchor_index: AnchorIndex,
    now: int,
    revocation: Optional[RevocationLog] = None,
    epoch: int = 0,
) -> PersonhoodTicket:
    """Gate an action: verify, require an anchor, check window + epoch-pinned non-revocation.

    Raises :class:`NotPersonError` (bad presentation / not enrolled),
    :class:`ExpiredError` (``now`` outside the validity window), or
    :class:`RevokedError` (anchor revoked at the pinned epoch).
    """
    admission = verifier.verify_presentation(scope, presentation)

    if not anchor_index.is_registered(scope, admission.scope_nullifier):
        raise NotPersonError("no personhood anchor for this scope (enroll first)")

    # The authoritative validity window + revocation pointer come from the committed
    # on-fabric anchor, NEVER from the (holder-controlled) presentation — otherwise a holder
    # could re-present the same secret with a forged wider window and vote past expiry.
    stored = anchor_index.anchor(scope, admission.scope_nullifier)
    if admission.holder_pairwise != stored["holder_pairwise"]:
        raise NotPersonError("presentation does not match the stored anchor")

    not_before, not_after = stored["not_before"], stored["not_after"]
    if not (not_before <= now < not_after):
        raise ExpiredError(f"now={now} outside validity [{not_before}, {not_after})")

    if revocation is not None:
        revocation_pointer = stored["revocation_pointer"]
        try:
            commitment, proof = revocation.prove_non_revocation(revocation_pointer, epoch)
        except KeyError as exc:  # pointer IS in the revoked set
            raise RevokedError("anchor is revoked at this epoch") from exc
        if not check_non_revocation(commitment, proof):
            raise RevokedError("non-revocation proof failed against the pinned commitment")

    return PersonhoodTicket(
        scope=scope,
        scope_nullifier=admission.scope_nullifier,
        pairwise_did=admission.pairwise_did,
        holder_pairwise=admission.holder_pairwise,
        not_before=not_before,
        not_after=not_after,
    )
