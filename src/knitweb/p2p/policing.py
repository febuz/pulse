"""Policing — turn detected/proven misbehavior into reputation consequences.

This is the glue that closes the **detect → prove → consequence** loop the web was missing:

  * detection / proof lives in ``fabric/equivocation`` (a verified equivocation report) and
    ``fabric/feed.check_conflict`` (two conflicting signed heads);
  * the consequence lives in ``p2p/reputation`` (a misbehavior ban-score).

Until now nothing connected them — an objectively-proven equivocation had no effect on a peer's
standing. These functions apply the right :class:`~knitweb.p2p.reputation.Offense` to the
offending key only when the evidence **verifies**, so a node never penalizes on hearsay: an
unverifiable equivocation report or a non-conflict is a no-op.

Identity note: the offending key in an equivocation/feed conflict is the feed's compressed public
key, which is the same identity space the reputation ledger is keyed on (a peer *is* its key), so
the penalty lands on the right peer. Pure, deterministic glue — no canonical/signed-record path.
"""

from __future__ import annotations

from ..fabric.equivocation import EquivocationReport, verify_equivocation_report
from ..fabric.feed import FeedHead, check_conflict
from .reputation import Offense, PeerReputation

__all__ = [
    "police_equivocation_report",
    "police_feed_conflict",
    "police_invalid_proof",
]


def police_equivocation_report(rep: PeerReputation, report: EquivocationReport) -> bool:
    """Verify ``report``; on success ban the offender. Returns whether it was acted on.

    Only a report that verifies *from its own bytes* (both embedded heads conflict under the
    feed key) triggers a penalty — unverifiable evidence is ignored, never penalized.
    ``Offense.EQUIVOCATION`` is full-threshold, so a single proven equivocation bans the offender.
    """
    if not verify_equivocation_report(report):
        return False
    rep.penalize(report.offender, Offense.EQUIVOCATION)
    return True


def police_feed_conflict(rep: PeerReputation, head_a: FeedHead, head_b: FeedHead) -> bool:
    """If ``head_a``/``head_b`` are a genuine conflict, penalize the offending feed key.

    Returns whether a penalty was applied. A non-conflict (different feeds, an unsigned head, a
    legitimate fork-bumped rewrite, or two consistent heads) is a no-op.
    """
    if not check_conflict(head_a, head_b):
        return False
    rep.penalize(head_a.feed, Offense.FEED_CONFLICT)
    return True


def police_invalid_proof(rep: PeerReputation, peer: str) -> bool:
    """Penalize a ``peer`` that served a stale/forged inclusion or range proof.

    The caller decides a served proof failed verification (``fabric/feed_proof`` /
    ``feed_multiproof``); this records the consequence. Returns whether the peer is now banned.
    """
    return rep.penalize(peer, Offense.STALE_OR_FORGED_PROOF)
