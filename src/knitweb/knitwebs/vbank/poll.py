"""vBank poll lifecycle — authority-defined polls and signed, auditable results.

A raw :func:`knitweb.knitwebs.vbank.tally.tally` is anonymous and unbounded: anyone could
publish a "result" and a ballot could carry any integer choice. This adds the two pieces a
real poll needs, both content-addressed and signed:

  * **`vbank-poll`** — the poll definition: the option count and the voting window, signed by
    the poll **authority**. It declares what a valid ballot looks like (``choice`` in
    ``0..options-1``) and when voting is open.
  * **`vbank-result`** — the certified outcome: the deterministic :func:`tally` over a ballot
    set, embedded with a link (``poll_cid``) to the definition and signed by the same
    authority. So the result is attributable and tamper-evident, and the included-ballot
    Merkle ``ballot_root`` keeps it publicly auditable.

Only the authority that defined a poll can certify its result (the signing key must match the
definition's ``authority``). Choices are range-checked against the declared option count.

Note (deferred): per-ballot voting-window enforcement needs a cast timestamp on the ballot;
the window lives in the definition now, and enforcing it at tally time is a later increment.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web
from .tally import tally

__all__ = ["POLL_KIND", "RESULT_KIND", "Poll", "VbankPoll", "verify_result", "audit_result"]

POLL_KIND = "vbank-poll"
RESULT_KIND = "vbank-result"


@dataclass(frozen=True)
class Poll:
    """A poll definition: an option count and a voting window for one ``poll_id``."""

    scope: str
    poll_id: str
    options: int     # valid choices are the integers 0 .. options-1
    opens_at: int    # epoch seconds (inclusive)
    closes_at: int   # epoch seconds (exclusive)
    quorum: int = 0  # minimum distinct voters for the result to be binding (0 = no quorum)

    def __post_init__(self) -> None:
        for name, value in (("options", self.options), ("opens_at", self.opens_at),
                            ("closes_at", self.closes_at), ("quorum", self.quorum)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"poll {name} must be an int")
        if self.options < 2:
            raise ValueError("a poll needs at least 2 options")
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        if self.quorum < 0:
            raise ValueError("quorum must be >= 0")
        if not self.poll_id:
            raise ValueError("poll_id must be non-empty")


class VbankPoll:
    """A poll authority: defines polls and certifies their deterministic results."""

    def __init__(self, authority_priv: str, scope: str) -> None:
        if not scope:
            raise ValueError("scope must be a non-empty string")
        self._priv = authority_priv
        self.authority_pub = crypto.public_from_private(authority_priv)
        self.authority = crypto.address(self.authority_pub)
        self.scope = scope

    def define(self, poll: Poll) -> Attestation:
        """Build and sign a ``vbank-poll`` definition record."""
        if poll.scope != self.scope:
            raise ValueError(f"poll scope {poll.scope!r} != authority scope {self.scope!r}")
        record = {
            "kind": POLL_KIND,
            "scope": poll.scope,
            "poll_id": poll.poll_id,
            "options": poll.options,
            "opens_at": poll.opens_at,
            "closes_at": poll.closes_at,
            "quorum": poll.quorum,
            "authority": self.authority,
        }
        canonical.encode(record)
        return attest(record, self._priv, author_field="authority")

    def certify_result(self, poll_record: dict, ballots: list[dict],
                       weights: dict | None = None) -> Attestation:
        """Validate ballots against the definition, tally them, and sign the result.

        ``poll_record`` is a ``vbank-poll`` definition this authority signed. Each in-window
        ballot's ``choice`` must be in ``0..options-1``. Raises ``ValueError`` otherwise. The
        deterministic computation lives in :func:`_result_record` so an auditor can recompute
        and check it independently (see :func:`verify_result`). ``weights`` (optional) makes it
        a fixed-point weighted tally.
        """
        if poll_record.get("authority") != self.authority:
            raise ValueError("only the defining authority may certify this poll's result")
        record = _result_record(poll_record, ballots, self.authority, weights)
        return attest(record, self._priv, author_field="authority")

    def weave_result(self, poll_record: dict, ballots: list[dict], web: Web,
                     weights: dict | None = None) -> tuple[str, Attestation]:
        """Certify and weave a result into ``web``; return (cid, attestation)."""
        att = self.certify_result(poll_record, ballots, weights)
        return web.weave(att.record), att


# ---------------------------------------------------------------------------
# Pure result computation + independent audit (no signing key required)
# ---------------------------------------------------------------------------

def _result_record(poll_record: dict, ballots: list[dict], authority_addr: str,
                   weights: dict | None = None) -> dict:
    """The deterministic ``vbank-result`` record for (poll, ballots) — pure, unsigned.

    Shared by :meth:`VbankPoll.certify_result` (which signs it) and :func:`verify_result`
    (which recomputes and compares), so a result is independently reproducible. ``weights``
    (optional fixed-point integer map) makes it a weighted tally; the result commits to a
    ``weight_root`` so the weighting is auditable too.
    """
    if poll_record.get("kind") != POLL_KIND:
        raise ValueError(f"not a {POLL_KIND}: {poll_record.get('kind')!r}")
    scope = poll_record["scope"]
    poll_id = poll_record["poll_id"]
    options = poll_record["options"]
    opens_at = poll_record["opens_at"]
    closes_at = poll_record["closes_at"]

    # Count only well-formed ballots cast inside the voting window. A malformed or out-of-range
    # ballot is SKIPPED, never fatal — otherwise one admitted bad ballot would block certification
    # of the whole poll (griefing), since the append-only fabric cannot prevent its emission.
    in_window = []
    for ballot in ballots:
        cast_at = ballot.get("cast_at")
        if not isinstance(cast_at, int) or isinstance(cast_at, bool):
            continue
        if not (opens_at <= cast_at < closes_at):
            continue
        choice = ballot.get("choice")
        if not isinstance(choice, int) or isinstance(choice, bool) or not (0 <= choice < options):
            continue
        in_window.append(ballot)

    counted = tally(scope, poll_id, in_window, weights)
    results = counted["results"]
    total_voters = counted["total_voters"]

    # Outcome: plurality winner with a deterministic smallest-option-id tie-break.
    if results:
        top = max(count for _choice, count in results)
        leaders = [choice for choice, count in results if count == top]
        winner, winner_votes, tie = min(leaders), top, len(leaders) > 1
    else:
        winner, winner_votes, tie = -1, 0, False
    quorum = poll_record.get("quorum", 0)

    record = {
        "kind": RESULT_KIND,
        "scope": scope,
        "poll_id": poll_id,
        "poll_cid": canonical.cid(poll_record),
        "authority": authority_addr,
        "total_voters": total_voters,
        "results": results,
        "ballot_root": counted["ballot_root"],
        "quorum": quorum,
        "quorum_met": total_voters >= quorum,
        "winner": winner,
        "winner_votes": winner_votes,
        "tie": tie,
        "weighted": counted["weighted"],
        "total_weight": counted["total_weight"],
        "weight_root": counted["weight_root"],
    }
    canonical.encode(record)
    return record


def verify_result(result_record: dict, poll_record: dict, ballots: list[dict],
                  weights: dict | None = None) -> bool:
    """True iff ``result_record`` is exactly what an honest authority certifies from
    ``poll_record`` + ``ballots`` (and ``weights`` for a weighted poll).

    Independent recomputation — anyone can run it to audit a published result. It does NOT
    check the signature (use the result's :class:`~knitweb.fabric.attest.Attestation` or
    :func:`audit_result` for that). The result's authority must be the poll's authority, and
    for a weighted result the auditor's ``weights`` must reproduce the committed ``weight_root``.
    """
    if not isinstance(result_record, dict) or not isinstance(poll_record, dict):
        return False
    if result_record.get("kind") != RESULT_KIND:
        return False
    if poll_record.get("authority") != result_record.get("authority"):
        return False
    try:
        expected = _result_record(poll_record, ballots, result_record["authority"], weights)
    except (ValueError, KeyError, TypeError):
        return False
    return expected == result_record


def audit_result(result_att: Attestation, poll_record: dict, ballots: list[dict],
                 weights: dict | None = None) -> bool:
    """Full audit: the result is validly authority-signed AND recomputes from the ballots."""
    return (
        result_att.verify(author_field="authority")
        and verify_result(result_att.record, poll_record, ballots, weights)
    )
