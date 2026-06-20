"""Deterministic, one-person-one-vote tally for vBank — the public audit-trail half.

The vBank guardrail (``docs/DOMAIN_KNITWEB_INTERFACE.md``) requires a *deterministic tally and
public audit trail*. This computes a result that is byte-for-byte reproducible by any peer from
the same ballot set, regardless of order:

  * **One person, one vote.** Ballots are deduped by ``scope_nullifier``. A voter may re-vote;
    the ballot with the highest ``seq`` wins, ties broken by the smallest ballot CID — both
    fully deterministic and order-independent (no timestamps, no trust in arrival order).
  * **Integer-only result.** Counts are integers; the result is a canonical, content-addressed
    ``vbank-tally`` record with a Merkle ``ballot_root`` over the *included* ballot CIDs, so the
    exact set of counted ballots is publicly auditable and tamper-evident.

This does NOT verify ballot signatures or personhood tickets — that is the gate's job at emit
time (:func:`knitweb.personhood.gate.require_personhood`). The tally operates on records already
admitted to the fabric; it only decides which of them count and produces the auditable result.
"""

from __future__ import annotations

from typing import Iterable, List

from ...core import canonical, crypto
from ...fabric.web import Web

__all__ = ["BALLOT_KIND", "TALLY_KIND", "tally", "collect_ballots"]

BALLOT_KIND = "vbank-ballot"
TALLY_KIND = "vbank-tally"


def collect_ballots(web: Web, scope: str, poll_id: str) -> List[dict]:
    """Read every ``vbank-ballot`` record for ``(scope, poll_id)`` out of a woven Web.

    Closes the fabric loop: ballots woven via :meth:`VbankKnitweb.weave` are read back for
    certification. Returned in deterministic CID order (the tally is order-independent
    regardless, but a stable order keeps downstream reproducible).
    """
    found = [
        record
        for record in web.nodes.values()
        if record.get("kind") == BALLOT_KIND
        and record.get("scope") == scope
        and record.get("poll_id") == poll_id
    ]
    found.sort(key=canonical.cid)
    return found


def tally(scope: str, poll_id: str, ballots: Iterable[dict],
          weights: dict | None = None) -> dict:
    """Return the deterministic ``vbank-tally`` record for ``ballots`` in one poll.

    ``ballots`` are ``vbank-ballot`` records (dicts). Every ballot must match ``scope`` and
    ``poll_id`` and carry an integer ``seq`` (the re-vote counter). Raises ``ValueError`` on a
    foreign-kind / wrong-scope / wrong-poll ballot.

    ``weights`` is an optional ``{scope_nullifier: weight}`` map of **non-negative fixed-point
    integers** (the authority chooses the scale). When given, each counted voter contributes
    their weight instead of 1 (a voter absent from the map weighs 0), and the result commits to
    a ``weight_root`` over the counted (nullifier, weight) pairs so a weighted tally stays
    independently auditable. When ``None``, it is one-person-one-vote.
    """
    if not scope or not poll_id:
        raise ValueError("scope and poll_id must be non-empty")

    # nullifier -> (seq, cid, choice) of the winning ballot for that voter
    winners: dict[str, tuple] = {}
    for ballot in ballots:
        if ballot.get("kind") != BALLOT_KIND:
            raise ValueError(f"not a {BALLOT_KIND}: {ballot.get('kind')!r}")
        if ballot.get("scope") != scope or ballot.get("poll_id") != poll_id:
            raise ValueError("ballot scope/poll_id does not match the tally")
        for field in ("scope_nullifier", "seq", "choice"):
            if field not in ballot:
                raise ValueError(f"ballot is missing required field {field!r}")
        nullifier = ballot["scope_nullifier"]
        seq = ballot["seq"]
        choice = ballot["choice"]
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise ValueError("ballot seq must be an int")
        cid = canonical.cid(ballot)
        current = winners.get(nullifier)
        # Highest seq wins; ties broken by the smallest CID (deterministic, order-independent).
        if current is None or seq > current[0] or (seq == current[0] and cid < current[1]):
            winners[nullifier] = (seq, cid, choice)

    counts: dict[int, int] = {}
    total_weight = 0
    weight_entries: List[tuple] = []  # (nullifier, weight) for the counted voters
    for nullifier, (_seq, _cid, choice) in winners.items():
        if weights is None:
            weight = 1
        else:
            weight = weights.get(nullifier, 0)
            if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
                raise ValueError("weights must be non-negative integers (fixed-point)")
        counts[choice] = counts.get(choice, 0) + weight
        total_weight += weight
        weight_entries.append((nullifier, weight))
    results: List[List[int]] = [[choice, counts[choice]] for choice in sorted(counts)]

    included_cids = sorted(cid for _seq, cid, _choice in winners.values())
    ballot_root = crypto.merkle_root(
        [crypto.sha256(cid.encode("utf-8")) for cid in included_cids]
    ).hex()

    if weights is None:
        weight_root = ""  # unweighted: one person, one vote
    else:
        weight_entries.sort()
        weight_root = crypto.merkle_root(
            [crypto.sha256(canonical.encode([nf, w])) for nf, w in weight_entries]
        ).hex()

    record = {
        "kind": TALLY_KIND,
        "scope": scope,
        "poll_id": poll_id,
        "total_voters": len(winners),
        "results": results,
        "ballot_root": ballot_root,
        "weighted": weights is not None,
        "total_weight": total_weight,
        "weight_root": weight_root,
    }
    canonical.encode(record)  # fail fast on any non-canonical content
    return record
