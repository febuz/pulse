"""Proofs for the vBank deterministic tally: one-person-one-vote, order-independent, auditable."""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.web import Web
from knitweb.knitwebs.vbank import BALLOT_KIND, TALLY_KIND, collect_ballots, tally

SCOPE = "vbank"
POLL = "p1"


def _nf(i: int) -> str:
    return crypto.sha256(f"voter-{i}".encode()).hex()


def _ballot(nullifier: str, choice: int, seq: int = 0, scope: str = SCOPE, poll: str = POLL) -> dict:
    return {
        "kind": BALLOT_KIND,
        "scope": scope,
        "poll_id": poll,
        "choice": choice,
        "actor": "pls1" + nullifier[:16],
        "scope_nullifier": nullifier,
        "seq": seq,
    }


@pytest.mark.property
def test_basic_counts():
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 1), _ballot(_nf(2), 0)]
    result = tally(SCOPE, POLL, ballots)
    assert result["kind"] == TALLY_KIND
    assert result["total_voters"] == 3
    assert result["results"] == [[0, 2], [1, 1]]


@pytest.mark.property
def test_tally_is_order_independent():
    ballots = [_ballot(_nf(i), i % 3) for i in range(7)]
    a = tally(SCOPE, POLL, ballots)
    b = tally(SCOPE, POLL, list(reversed(ballots)))
    assert canonical.cid(a) == canonical.cid(b)


@pytest.mark.property
def test_one_person_one_vote_highest_seq_wins():
    # voter 0 re-votes: seq 0 -> choice 0, then seq 1 -> choice 1 (the later vote counts)
    ballots = [
        _ballot(_nf(0), 0, seq=0),
        _ballot(_nf(0), 1, seq=1),
        _ballot(_nf(1), 0, seq=0),
    ]
    result = tally(SCOPE, POLL, ballots)
    assert result["total_voters"] == 2          # two distinct people, not three ballots
    assert result["results"] == [[0, 1], [1, 1]]  # voter 0 counted as choice 1


@pytest.mark.property
def test_same_seq_tie_break_is_deterministic():
    # a malformed double-vote at the same seq must resolve identically every run
    ballots = [_ballot(_nf(0), 0, seq=5), _ballot(_nf(0), 9, seq=5)]
    a = tally(SCOPE, POLL, ballots)
    b = tally(SCOPE, POLL, list(reversed(ballots)))
    assert a == b
    assert a["total_voters"] == 1               # still one person


@pytest.mark.property
def test_empty_poll():
    result = tally(SCOPE, POLL, [])
    assert result["total_voters"] == 0
    assert result["results"] == []


@pytest.mark.property
def test_foreign_kind_rejected():
    bad = dict(_ballot(_nf(0), 0), kind="not-a-ballot")
    with pytest.raises(ValueError):
        tally(SCOPE, POLL, [bad])


@pytest.mark.property
def test_wrong_scope_or_poll_rejected():
    with pytest.raises(ValueError):
        tally(SCOPE, POLL, [_ballot(_nf(0), 0, scope="other")])
    with pytest.raises(ValueError):
        tally(SCOPE, POLL, [_ballot(_nf(0), 0, poll="other-poll")])


@pytest.mark.property
def test_collect_ballots_from_web_then_tally():
    web = Web()
    b0, b1 = _ballot(_nf(0), 0), _ballot(_nf(1), 1)
    web.weave(b0)
    web.weave(b1)
    web.weave({"kind": "knowledge-item", "scope": SCOPE, "poll_id": POLL})  # noise: not a ballot
    web.weave(dict(_ballot(_nf(2), 0), poll_id="other-poll"))               # other poll
    web.weave(dict(_ballot(_nf(3), 1), scope="other-scope"))                # other scope

    collected = collect_ballots(web, SCOPE, POLL)
    assert len(collected) == 2
    # tallying the collected set equals tallying the originals (closes the fabric loop)
    assert tally(SCOPE, POLL, collected) == tally(SCOPE, POLL, [b0, b1])


@pytest.mark.property
def test_tally_missing_field_raises_value_error():
    # documented contract: ValueError (not a bare KeyError) on a malformed ballot
    bad = {"kind": BALLOT_KIND, "scope": SCOPE, "poll_id": POLL}  # no seq/choice/nullifier
    with pytest.raises(ValueError):
        tally(SCOPE, POLL, [bad])


@pytest.mark.property
def test_collect_ballots_empty_web():
    assert collect_ballots(Web(), SCOPE, POLL) == []


@pytest.mark.property
def test_ballot_root_commits_to_the_included_set():
    base = [_ballot(_nf(0), 0), _ballot(_nf(1), 1)]
    root_a = tally(SCOPE, POLL, base)["ballot_root"]
    # same included set (re-vote loser excluded) -> same root
    plus_loser = base + [_ballot(_nf(0), 0, seq=0)]  # duplicate of voter 0's only ballot
    assert tally(SCOPE, POLL, plus_loser)["ballot_root"] == root_a
    # a genuinely different included set -> different root
    root_b = tally(SCOPE, POLL, base + [_ballot(_nf(2), 1)])["ballot_root"]
    assert root_b != root_a
    assert crypto.is_valid_hex(root_a, 32)
