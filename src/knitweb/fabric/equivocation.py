"""Equivocation fraud proofs — turn the core P2P attack into an objective, slashable record.

``fabric/feed.py`` calls equivocation — an author signing two *different* histories at the same
position — "the core P2P attack", and can *detect* it (:func:`feed.check_conflict`): two
validly-signed heads from the same feed at the same ``(length, fork)`` but different ``root``.
What was missing is a way to **package that detection as portable evidence**: a canonical,
content-addressed record that *anyone* who observed the two heads can publish, and that *any*
third party can verify from the record alone — without trusting the reporter.

That is what this module adds, mirroring proof-of-stake slashing (Ethereum's double-sign /
surround-vote evidence): an :class:`EquivocationReport` embeds both offending signed heads; its
verification re-checks, from the bytes, that the two heads are genuinely a conflict by the same
key. A valid report names the offending ``feed`` key as **objectively slashable** — the bridge
between feed safety and the staking economics (collateral sizing, ``pouw/collateral.py``):
equivocation now has a provable, third-party-submittable consequence.

Honest authorship is untouched: a legitimate truncate-and-rewrite **bumps the fork counter**
(``feed.Feed.truncate``), so it is *not* a conflict and cannot be reported as equivocation. The
two embedded heads are stored in a canonical order (by root) so the same equivocation yields one
content-addressed report regardless of which head was seen first. No floats, integer/​hex only;
introduces a new ``equivocation-report`` record kind (additive — no existing record changes).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core import canonical
from .feed import FeedHead, check_conflict

__all__ = [
    "EquivocationReport",
    "prove_equivocation",
    "verify_equivocation_report",
]

_HEAD_FIELDS = ("root", "length", "fork", "sig")


def _head_fields(head: FeedHead) -> dict:
    return {"root": head.root, "length": head.length, "fork": head.fork, "sig": head.sig}


def _head_from_fields(feed: str, d: dict) -> FeedHead:
    return FeedHead(feed=feed, root=d["root"], length=d["length"], fork=d["fork"], sig=d["sig"])


@dataclass(frozen=True)
class EquivocationReport:
    """Portable evidence that ``feed`` signed two conflicting histories at one position."""

    feed: str          # the offending author's feed key (compressed secp256k1 pubkey hex)
    head_a: dict       # one signed head's fields {root, length, fork, sig}
    head_b: dict       # the other; head_a/head_b are stored sorted by root (canonical)
    reporter: str      # address that submitted the evidence (may earn a bounty on slash)

    def to_record(self) -> dict:
        record = {
            "kind": "equivocation-report",
            "feed": self.feed,
            "head_a": {k: self.head_a[k] for k in _HEAD_FIELDS},
            "head_b": {k: self.head_b[k] for k in _HEAD_FIELDS},
            "reporter": self.reporter,
        }
        canonical.encode(record)  # fail fast on any non-canonical content
        return record

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())

    @property
    def offender(self) -> str:
        """The feed key whose stake is slashable for this equivocation."""
        return self.feed


def prove_equivocation(
    head_a: FeedHead, head_b: FeedHead, reporter: str
) -> Optional[EquivocationReport]:
    """Build a report iff ``(head_a, head_b)`` genuinely prove ``head_a.feed`` equivocated.

    Returns ``None`` when the pair is *not* a conflict — different feeds, an unsigned/forged
    head, a legitimate fork-bumped rewrite, or simply two non-conflicting heads — so an honest
    author can never be reported. The two heads are stored sorted by root for a canonical report.
    """
    if not check_conflict(head_a, head_b):
        return None
    fa, fb = _head_fields(head_a), _head_fields(head_b)
    if fb["root"] < fa["root"]:                 # canonical order: smaller root first
        fa, fb = fb, fa
    return EquivocationReport(feed=head_a.feed, head_a=fa, head_b=fb, reporter=reporter)


def verify_equivocation_report(report: EquivocationReport) -> bool:
    """True iff the report's embedded heads, on their own, prove equivocation by ``report.feed``.

    Trustless: it reconstructs both heads from the record and re-runs :func:`feed.check_conflict`
    (which re-verifies *both signatures* against ``feed``), so a forged or tampered report fails.
    """
    try:
        a = _head_from_fields(report.feed, report.head_a)
        b = _head_from_fields(report.feed, report.head_b)
    except (KeyError, TypeError):
        return False
    return check_conflict(a, b)
