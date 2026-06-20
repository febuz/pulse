"""Liquid (delegated) voting for vBank — a voter may delegate their weight to another.

A delegation is a signed record (gated by the delegator's personhood ticket, like a ballot)
naming the delegate by scope nullifier. At tally time, weight flows along delegation chains to
whoever actually voted:

  * **direct vote overrides delegation** — if you cast a ballot, your weight goes to your choice
    regardless of any delegation you also made;
  * **transitive** — A→B→C resolves to C's choice if C voted;
  * **cycle / dead-end abstains** — a chain that loops, or ends at someone who never voted, does
    not count (its weight is dropped).

This composes with fixed-point weights: each participant (anyone who voted or delegated) carries
their weight to the choice their chain resolves to. Authenticity comes from the signed
delegation record (only the delegator can delegate their own nullifier).

Note: this module computes a liquid result; wiring it into ``poll.certify_result`` (so an
authority signs a delegated result) is a thin follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web
from ...personhood.gate import PersonhoodTicket
from .poll import POLL_KIND
from .tally import BALLOT_KIND

__all__ = [
    "DELEGATION_KIND",
    "LIQUID_RESULT_KIND",
    "Delegation",
    "emit_delegation",
    "collect_delegations",
    "delegation_map",
    "resolve_liquid",
    "liquid_result_record",
    "certify_liquid_result",
    "verify_liquid_result",
    "audit_liquid_result",
]

DELEGATION_KIND = "vbank-delegation"
LIQUID_RESULT_KIND = "vbank-liquid-result"


@dataclass(frozen=True)
class Delegation:
    """One delegation: ``delegator`` hands their voting weight to ``delegate_nullifier``."""

    scope: str
    poll_id: str
    delegator: str            # pls1 pairwise address of the delegator (signs)
    delegator_nullifier: str  # the delegator's scope nullifier
    delegate_nullifier: str   # the nullifier they delegate to
    seq: int = 0              # re-delegation counter (highest seq wins)

    def __post_init__(self) -> None:
        if not isinstance(self.seq, int) or isinstance(self.seq, bool) or self.seq < 0:
            raise ValueError("delegation seq must be a non-negative int")
        if self.delegator_nullifier == self.delegate_nullifier:
            raise ValueError("cannot delegate to yourself")


def emit_delegation(delegation: Delegation, ticket: PersonhoodTicket, delegator_priv: str) -> Attestation:
    """Build and sign a ``vbank-delegation`` record, gated by the delegator's ticket."""
    if not isinstance(ticket, PersonhoodTicket):
        raise TypeError("a PersonhoodTicket is required to delegate")
    if ticket.scope != delegation.scope:
        raise ValueError("ticket scope does not match the delegation")
    if ticket.scope_nullifier != delegation.delegator_nullifier:
        raise ValueError("ticket nullifier does not authorise this delegation")
    if ticket.holder_pairwise != delegation.delegator:
        raise ValueError("ticket holder does not match the delegator")
    record = {
        "kind": DELEGATION_KIND,
        "scope": delegation.scope,
        "poll_id": delegation.poll_id,
        "actor": delegation.delegator,
        "scope_nullifier": delegation.delegator_nullifier,
        "delegate_nullifier": delegation.delegate_nullifier,
        "seq": delegation.seq,
    }
    if not crypto.is_valid_address(record["actor"]):
        raise ValueError("delegator must be a current PLS address")
    canonical.encode(record)
    return attest(record, delegator_priv, author_field="actor")


def collect_delegations(web: Web, scope: str, poll_id: str) -> List[dict]:
    """Read every ``vbank-delegation`` record for ``(scope, poll_id)`` from a woven Web."""
    found = [
        record
        for record in web.nodes.values()
        if record.get("kind") == DELEGATION_KIND
        and record.get("scope") == scope
        and record.get("poll_id") == poll_id
    ]
    found.sort(key=canonical.cid)
    return found


def _direct_choices(ballots: List[dict]) -> Dict[str, int]:
    """Deduped {scope_nullifier: choice} from ballot records (highest seq, tie smallest CID)."""
    winners: Dict[str, tuple] = {}
    for ballot in ballots:
        if ballot.get("kind") != BALLOT_KIND:
            raise ValueError(f"not a {BALLOT_KIND}: {ballot.get('kind')!r}")
        nullifier = ballot["scope_nullifier"]
        seq = ballot["seq"]
        cid = canonical.cid(ballot)
        current = winners.get(nullifier)
        if current is None or seq > current[0] or (seq == current[0] and cid < current[1]):
            winners[nullifier] = (seq, cid, ballot["choice"])
    return {nf: choice for nf, (_seq, _cid, choice) in winners.items()}


def delegation_map(delegations: List[dict]) -> Dict[str, str]:
    """Deduped {delegator_nullifier: delegate_nullifier} (highest seq, tie smallest CID)."""
    winners: Dict[str, tuple] = {}
    for record in delegations:
        if record.get("kind") != DELEGATION_KIND:
            raise ValueError(f"not a {DELEGATION_KIND}: {record.get('kind')!r}")
        delegator = record["scope_nullifier"]
        seq = record["seq"]
        cid = canonical.cid(record)
        current = winners.get(delegator)
        if current is None or seq > current[0] or (seq == current[0] and cid < current[1]):
            winners[delegator] = (seq, cid, record["delegate_nullifier"])
    return {nf: target for nf, (_seq, _cid, target) in winners.items()}


def _counted_root(records: List[dict]) -> str:
    """Merkle root over the CIDs of the COUNTED records (dedup by scope_nullifier, highest seq,
    tie smallest CID) — the same winners the tally uses — so a liquid result commits to exactly
    which ballots/delegations it counted, like ballot_root/pledge_root elsewhere."""
    winners: Dict[str, tuple] = {}
    for record in records:
        key = record["scope_nullifier"]
        seq = record["seq"]
        cid = canonical.cid(record)
        current = winners.get(key)
        if current is None or seq > current[0] or (seq == current[0] and cid < current[1]):
            winners[key] = (seq, cid)
    cids = sorted(cid for _seq, cid in winners.values())
    return crypto.merkle_root([crypto.sha256(c.encode("utf-8")) for c in cids]).hex()


def resolve_liquid(direct_choices: Dict[str, int], delegations: Dict[str, str],
                   weights: Dict[str, int] | None = None) -> Dict[int, int]:
    """Resolve liquid-democracy weight flow to ``{choice: total_weight}``.

    Each participant (anyone who voted or delegated) carries their weight to the choice their
    delegation chain resolves to; voting directly wins; cycles/dead-ends abstain.
    """
    participants = set(direct_choices) | set(delegations)
    counts: Dict[int, int] = {}
    for participant in participants:
        if weights is None:
            weight = 1
        else:
            weight = weights.get(participant, 0)
            if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
                raise ValueError("weights must be non-negative integers")
        if weight == 0:
            continue
        # follow the chain to a direct voter (with cycle detection)
        seen = set()
        cursor = participant
        choice = None
        while cursor is not None and cursor not in seen:
            if cursor in direct_choices:
                choice = direct_choices[cursor]
                break
            seen.add(cursor)
            cursor = delegations.get(cursor)
        if choice is not None:
            counts[choice] = counts.get(choice, 0) + weight
    return counts


def liquid_result_record(poll_record: dict, ballots: List[dict], delegations: List[dict],
                         authority_addr: str, weights: Dict[str, int] | None = None) -> dict:
    """The deterministic ``vbank-liquid-result`` record — pure, unsigned.

    Counts only in-window, choice-valid direct ballots, then flows delegated weight via
    :func:`resolve_liquid`. Shared by :func:`certify_liquid_result` (signs) and
    :func:`verify_liquid_result` (recomputes), so a delegated result is independently auditable.
    """
    if poll_record.get("kind") != POLL_KIND:
        raise ValueError(f"not a {POLL_KIND}: {poll_record.get('kind')!r}")
    scope = poll_record["scope"]
    poll_id = poll_record["poll_id"]
    options = poll_record["options"]
    opens_at = poll_record["opens_at"]
    closes_at = poll_record["closes_at"]

    # Count only well-formed, in-window ballots for THIS poll; skip anything else (a single
    # malformed/foreign admitted ballot must not block certification — same rule as delegations).
    in_window: List[dict] = []
    for ballot in ballots:
        if ballot.get("kind") != BALLOT_KIND:
            continue
        if ballot.get("scope") != scope or ballot.get("poll_id") != poll_id:
            continue
        cast_at = ballot.get("cast_at")
        if not isinstance(cast_at, int) or isinstance(cast_at, bool):
            continue
        if not (opens_at <= cast_at < closes_at):
            continue
        choice = ballot.get("choice")
        if not isinstance(choice, int) or isinstance(choice, bool) or not (0 <= choice < options):
            continue
        in_window.append(ballot)

    direct = _direct_choices(in_window)
    # Bind delegations to THIS poll, exactly as ballots are bound above — otherwise a delegation
    # signed for a different poll/scope would inflate this tally yet still pass independent audit.
    scoped = [d for d in delegations if d.get("scope") == scope and d.get("poll_id") == poll_id]
    deleg = delegation_map(scoped)
    counts = resolve_liquid(direct, deleg, weights)
    results = [[choice, counts[choice]] for choice in sorted(counts)]
    if results:
        top = max(count for _choice, count in results)
        leaders = [choice for choice, count in results if count == top]
        winner, winner_votes, tie = min(leaders), top, len(leaders) > 1
    else:
        winner, winner_votes, tie = -1, 0, False

    record = {
        "kind": LIQUID_RESULT_KIND,
        "scope": scope,
        "poll_id": poll_id,
        "poll_cid": canonical.cid(poll_record),
        "authority": authority_addr,
        "results": results,
        "direct_voters": len(direct),
        "delegations": len(deleg),
        "total_weight": sum(counts.values()),
        "winner": winner,
        "winner_votes": winner_votes,
        "tie": tie,
        "ballot_root": _counted_root(in_window),
        "delegation_root": _counted_root(scoped),
    }
    canonical.encode(record)
    return record


def certify_liquid_result(poll_record: dict, ballots: List[dict], delegations: List[dict],
                          authority_priv: str, weights: Dict[str, int] | None = None) -> Attestation:
    """Sign a liquid result (only the poll's defining authority may certify it)."""
    authority_addr = crypto.address(crypto.public_from_private(authority_priv))
    if poll_record.get("authority") != authority_addr:
        raise ValueError("only the defining authority may certify this liquid result")
    record = liquid_result_record(poll_record, ballots, delegations, authority_addr, weights)
    return attest(record, authority_priv, author_field="authority")


def verify_liquid_result(result_record: dict, poll_record: dict, ballots: List[dict],
                         delegations: List[dict], weights: Dict[str, int] | None = None) -> bool:
    """True iff ``result_record`` is the honest liquid result for these inputs (recomputation)."""
    if not isinstance(result_record, dict) or not isinstance(poll_record, dict):
        return False
    if result_record.get("kind") != LIQUID_RESULT_KIND:
        return False
    if poll_record.get("authority") != result_record.get("authority"):
        return False
    try:
        expected = liquid_result_record(poll_record, ballots, delegations,
                                        result_record["authority"], weights)
    except (ValueError, KeyError, TypeError):
        return False
    return expected == result_record


def audit_liquid_result(result_att: Attestation, poll_record: dict, ballots: List[dict],
                        delegations: List[dict], weights: Dict[str, int] | None = None) -> bool:
    """Full audit: the liquid result is validly authority-signed AND recomputes from the inputs."""
    return (
        result_att.verify(author_field="authority")
        and verify_liquid_result(result_att.record, poll_record, ballots, delegations, weights)
    )
