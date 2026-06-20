"""Ranked-choice (instant-runoff) voting for vBank.

A ranked ballot lists options in preference order instead of a single choice. The tally is
instant-runoff (IRV): count each voter's highest-ranked still-active option; if one option has a
strict majority of active votes it wins, otherwise eliminate the lowest option (deterministic
smallest-id tie-break) and recount, redistributing each ballot to its next surviving preference.
Ballots whose preferences are all eliminated are *exhausted* and drop out. Composes with
fixed-point weights.

Like every vBank result, IRV is deterministic and reproducible: same ballots ⇒ same rounds and
winner. Authority-certification of a ranked result mirrors the other result types and is a thin
follow-up; this module provides the gated ballot, the read-model, and the pure algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web
from ...personhood.gate import PersonhoodTicket
from .poll import POLL_KIND

__all__ = [
    "RANKED_BALLOT_KIND",
    "RANKED_RESULT_KIND",
    "RankedBallot",
    "emit_ranked_ballot",
    "collect_ranked_ballots",
    "instant_runoff",
    "ranked_result_record",
    "certify_ranked_result",
    "verify_ranked_result",
    "audit_ranked_result",
]

RANKED_BALLOT_KIND = "vbank-ranked-ballot"
RANKED_RESULT_KIND = "vbank-ranked-result"


@dataclass(frozen=True)
class RankedBallot:
    """One ranked vote: ``ranking`` is option ids in descending preference (distinct)."""

    scope: str
    poll_id: str
    ranking: Tuple[int, ...]
    voter: str
    scope_nullifier: str
    seq: int = 0
    cast_at: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.ranking, tuple) or not self.ranking:
            raise TypeError("ranking must be a non-empty tuple of option ids")
        for option in self.ranking:
            if not isinstance(option, int) or isinstance(option, bool) or option < 0:
                raise ValueError("ranking entries must be non-negative ints")
        if len(set(self.ranking)) != len(self.ranking):
            raise ValueError("ranking must not repeat an option")
        for name, value in (("seq", self.seq), ("cast_at", self.cast_at)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"ranked ballot {name} must be a non-negative int")


def emit_ranked_ballot(ballot: RankedBallot, ticket: PersonhoodTicket, voter_priv: str) -> Attestation:
    """Build and sign a ``vbank-ranked-ballot`` record, gated by the voter's ticket."""
    if not isinstance(ticket, PersonhoodTicket):
        raise TypeError("a PersonhoodTicket is required to cast a ranked ballot")
    if ticket.scope != ballot.scope:
        raise ValueError("ticket scope does not match the ballot")
    if ticket.scope_nullifier != ballot.scope_nullifier:
        raise ValueError("ticket nullifier does not authorise this ballot")
    if ticket.holder_pairwise != ballot.voter:
        raise ValueError("ticket holder does not match the ballot voter")
    record = {
        "kind": RANKED_BALLOT_KIND,
        "scope": ballot.scope,
        "poll_id": ballot.poll_id,
        "ranking": list(ballot.ranking),
        "actor": ballot.voter,
        "scope_nullifier": ballot.scope_nullifier,
        "seq": ballot.seq,
        "cast_at": ballot.cast_at,
    }
    if not crypto.is_valid_address(record["actor"]):
        raise ValueError("ranked ballot actor must be a current PLS address")
    canonical.encode(record)
    return attest(record, voter_priv, author_field="actor")


def collect_ranked_ballots(web: Web, scope: str, poll_id: str) -> List[dict]:
    """Read every ``vbank-ranked-ballot`` record for ``(scope, poll_id)`` from a woven Web."""
    found = [
        record
        for record in web.nodes.values()
        if record.get("kind") == RANKED_BALLOT_KIND
        and record.get("scope") == scope
        and record.get("poll_id") == poll_id
    ]
    found.sort(key=canonical.cid)
    return found


def _ranked_choices(ballots: List[dict], options: int) -> Dict[str, Tuple[int, ...]]:
    """Deduped {nullifier: ranking} (highest seq, tie smallest CID); rankings validated."""
    winners: Dict[str, tuple] = {}
    for ballot in ballots:
        if ballot.get("kind") != RANKED_BALLOT_KIND:
            raise ValueError(f"not a {RANKED_BALLOT_KIND}: {ballot.get('kind')!r}")
        ranking = ballot.get("ranking")
        if not isinstance(ranking, (list, tuple)) or not ranking:
            raise ValueError("ranking must be a non-empty list")
        if len(set(ranking)) != len(ranking):
            raise ValueError("ranking must not repeat an option")
        for option in ranking:
            if not isinstance(option, int) or isinstance(option, bool) or not (0 <= option < options):
                raise ValueError(f"ranked option {option!r} out of range 0..{options - 1}")
        nullifier = ballot["scope_nullifier"]
        seq = ballot["seq"]
        cid = canonical.cid(ballot)
        current = winners.get(nullifier)
        if current is None or seq > current[0] or (seq == current[0] and cid < current[1]):
            winners[nullifier] = (seq, cid, tuple(ranking))
    return {nf: ranking for nf, (_seq, _cid, ranking) in winners.items()}


def instant_runoff(ballots: List[dict], options: int,
                   weights: Dict[str, int] | None = None) -> dict:
    """Run instant-runoff over ranked ballots; return a deterministic ``vbank-ranked-result``.

    The result lists each round's tallies (``rounds``), the eliminated option per round, the
    ``winner`` (or -1 if everyone is exhausted) and the ``winner_round``.
    """
    rankings = _ranked_choices(ballots, options)

    def weight_of(nullifier: str) -> int:
        if weights is None:
            return 1
        weight = weights.get(nullifier, 0)
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
            raise ValueError("weights must be non-negative integers")
        return weight

    eliminated: set = set()
    rounds: List[dict] = []
    winner, winner_round, tie = -1, -1, False

    while True:
        active = [c for c in range(options) if c not in eliminated]
        round_counts = {c: 0 for c in active}
        total = 0
        for nullifier, ranking in rankings.items():
            weight = weight_of(nullifier)
            if weight == 0:
                continue
            top = next((c for c in ranking if c not in eliminated), None)
            if top is None:
                continue  # exhausted ballot
            round_counts[top] += weight
            total += weight

        if total == 0:
            rounds.append({"counts": sorted([[c, 0] for c in active]), "eliminated": -1})
            break

        leader = max(active, key=lambda c: (round_counts[c], -c))  # most votes, tie smallest id
        if round_counts[leader] * 2 > total:
            rounds.append({"counts": sorted([[c, round_counts[c]] for c in active]), "eliminated": -1})
            winner, winner_round = leader, len(rounds) - 1
            break
        if len(active) <= 1:
            rounds.append({"counts": sorted([[c, round_counts[c]] for c in active]), "eliminated": -1})
            winner, winner_round = leader, len(rounds) - 1
            break

        # Perfect tie among all remaining candidates: elimination cannot break it meaningfully, so
        # award the smallest option id (consistent with plurality/liquid) and flag the tie rather
        # than silently eliminating the smaller id and crowning the larger one.
        if min(round_counts.values()) == max(round_counts.values()):
            rounds.append({"counts": sorted([[c, round_counts[c]] for c in active]), "eliminated": -1})
            winner, winner_round, tie = min(active), len(rounds) - 1, True
            break

        loser = min(active, key=lambda c: (round_counts[c], c))  # fewest votes, tie smallest id
        rounds.append({"counts": sorted([[c, round_counts[c]] for c in active]), "eliminated": loser})
        eliminated.add(loser)

    record = {
        "options": options,
        "voters": len(rankings),
        "rounds": rounds,
        "winner": winner,
        "winner_round": winner_round,
        "tie": tie,
    }
    canonical.encode(record)
    return record


def _in_window_ranked(poll_record: dict, ballots: List[dict]) -> List[dict]:
    """Well-formed ranked ballots for THIS poll, cast inside its window.

    A malformed/foreign/out-of-range ranked ballot is SKIPPED, never fatal — one admitted bad
    ballot must not block certification (the append-only fabric cannot prevent its emission). The
    ranking range is validated here too, so the downstream tally never raises on a bad ranking.
    """
    if poll_record.get("kind") != POLL_KIND:
        raise ValueError(f"not a {POLL_KIND}: {poll_record.get('kind')!r}")
    scope = poll_record["scope"]
    poll_id = poll_record["poll_id"]
    options = poll_record["options"]
    opens_at = poll_record["opens_at"]
    closes_at = poll_record["closes_at"]
    in_window: List[dict] = []
    for ballot in ballots:
        if ballot.get("kind") != RANKED_BALLOT_KIND:
            continue
        if ballot.get("scope") != scope or ballot.get("poll_id") != poll_id:
            continue
        cast_at = ballot.get("cast_at")
        if not isinstance(cast_at, int) or isinstance(cast_at, bool):
            continue
        if not (opens_at <= cast_at < closes_at):
            continue
        ranking = ballot.get("ranking")
        if not isinstance(ranking, (list, tuple)) or not ranking:
            continue
        if len(set(ranking)) != len(ranking):
            continue
        if any(not isinstance(o, int) or isinstance(o, bool) or not (0 <= o < options) for o in ranking):
            continue
        in_window.append(ballot)
    return in_window


def ranked_result_record(poll_record: dict, ballots: List[dict], authority_addr: str,
                         weights: Dict[str, int] | None = None) -> dict:
    """The deterministic ``vbank-ranked-result`` record — pure, unsigned, poll-linked."""
    in_window = _in_window_ranked(poll_record, ballots)
    irv = instant_runoff(in_window, poll_record["options"], weights)
    record = {
        "kind": RANKED_RESULT_KIND,
        "scope": poll_record["scope"],
        "poll_id": poll_record["poll_id"],
        "poll_cid": canonical.cid(poll_record),
        "authority": authority_addr,
        "options": irv["options"],
        "voters": irv["voters"],
        "rounds": irv["rounds"],
        "winner": irv["winner"],
        "winner_round": irv["winner_round"],
        "tie": irv["tie"],
    }
    canonical.encode(record)
    return record


def certify_ranked_result(poll_record: dict, ballots: List[dict], authority_priv: str,
                          weights: Dict[str, int] | None = None) -> Attestation:
    """Sign a ranked (IRV) result (only the poll's defining authority may certify it)."""
    authority_addr = crypto.address(crypto.public_from_private(authority_priv))
    if poll_record.get("authority") != authority_addr:
        raise ValueError("only the defining authority may certify this ranked result")
    record = ranked_result_record(poll_record, ballots, authority_addr, weights)
    return attest(record, authority_priv, author_field="authority")


def verify_ranked_result(result_record: dict, poll_record: dict, ballots: List[dict],
                         weights: Dict[str, int] | None = None) -> bool:
    """True iff ``result_record`` is the honest IRV result for these inputs (recomputation)."""
    if not isinstance(result_record, dict) or not isinstance(poll_record, dict):
        return False
    if result_record.get("kind") != RANKED_RESULT_KIND:
        return False
    if poll_record.get("authority") != result_record.get("authority"):
        return False
    try:
        expected = ranked_result_record(poll_record, ballots, result_record["authority"], weights)
    except (ValueError, KeyError, TypeError):
        return False
    return expected == result_record


def audit_ranked_result(result_att: Attestation, poll_record: dict, ballots: List[dict],
                        weights: Dict[str, int] | None = None) -> bool:
    """Full audit: the ranked result is validly authority-signed AND recomputes from the inputs."""
    return (
        result_att.verify(author_field="authority")
        and verify_ranked_result(result_att.record, poll_record, ballots, weights)
    )
