"""Proofs for ranked-choice (instant-runoff) voting: redistribution, exhaustion, ties, weights."""

import pytest

from knitweb.core import crypto
from knitweb.knitwebs.vbank import (
    RANKED_BALLOT_KIND,
    RANKED_RESULT_KIND,
    Poll,
    RankedBallot,
    VbankPoll,
    audit_ranked_result,
    certify_ranked_result,
    collect_ranked_ballots,
    emit_ranked_ballot,
    instant_runoff,
    verify_ranked_result,
)
from knitweb.fabric.web import Web
from knitweb.personhood.gate import PersonhoodTicket

SCOPE = "vbank"
POLL = "irv-1"


def _nf(name: str) -> str:
    return crypto.sha256(name.encode()).hex()


def _rb(nf: str, ranking, seq: int = 0) -> dict:
    return {
        "kind": RANKED_BALLOT_KIND, "scope": SCOPE, "poll_id": POLL, "ranking": list(ranking),
        "actor": "pls1" + nf[:16], "scope_nullifier": nf, "seq": seq, "cast_at": 1,
    }


def _total(round_entry) -> int:
    return sum(n for _c, n in round_entry["counts"])


@pytest.mark.property
def test_first_round_majority_wins():
    ballots = [_rb(_nf("a"), [0, 1]), _rb(_nf("b"), [0, 2]), _rb(_nf("c"), [1, 0])]
    res = instant_runoff(ballots, options=3)
    assert res["winner"] == 0 and res["winner_round"] == 0
    assert res["voters"] == 3


@pytest.mark.property
def test_elimination_and_redistribution():
    # 0:2 first, 1:2 first, 2:1 first -> eliminate 2; its [2,0] ballot moves to 0 -> 0 wins
    ballots = [_rb(_nf("a"), [0]), _rb(_nf("b"), [0]), _rb(_nf("c"), [1]),
               _rb(_nf("d"), [1]), _rb(_nf("e"), [2, 0])]
    res = instant_runoff(ballots, options=3)
    assert res["winner"] == 0 and res["winner_round"] == 1
    assert res["rounds"][0]["eliminated"] == 2     # lowest first-pref eliminated
    assert res["rounds"][1]["eliminated"] == -1     # winner found, no elimination


@pytest.mark.property
def test_exhausted_ballot_drops_out():
    # voter who ranked only option 0; once 0 is eliminated they exhaust (active total shrinks)
    ballots = [_rb(_nf("a"), [2]), _rb(_nf("b"), [2]), _rb(_nf("c"), [0]), _rb(_nf("d"), [1])]
    res = instant_runoff(ballots, options=3)
    assert res["rounds"][0]["eliminated"] == 0      # 0 has the fewest (tie at 1 -> smallest id)
    assert _total(res["rounds"][0]) == 4
    assert _total(res["rounds"][1]) == 3            # the [0] voter exhausted -> total drops
    assert res["winner"] == 2


@pytest.mark.property
def test_perfect_tie_is_flagged_and_smallest_id_wins():
    # A perfect tie is surfaced (tie=True) and resolved to the smallest option id — consistent
    # with plurality/liquid — rather than silently eliminating the smaller id and crowning a larger.
    ballots = [_rb(_nf("a"), [0]), _rb(_nf("b"), [1]), _rb(_nf("c"), [2])]
    res = instant_runoff(ballots, options=3)
    assert res["winner"] == 0 and res["tie"] is True
    assert res["rounds"][0]["eliminated"] == -1


@pytest.mark.property
def test_two_way_perfect_tie_flagged():
    ballots = [_rb(_nf("a"), [0]), _rb(_nf("b"), [1])]
    res = instant_runoff(ballots, options=2)
    assert res["winner"] == 0 and res["tie"] is True


@pytest.mark.property
def test_clear_winner_is_not_flagged_as_tie():
    res = instant_runoff([_rb(_nf("a"), [0]), _rb(_nf("b"), [0]), _rb(_nf("c"), [1])], options=2)
    assert res["winner"] == 0 and res["tie"] is False


@pytest.mark.property
def test_weighted_instant_runoff():
    ballots = [_rb(_nf("a"), [0]), _rb(_nf("b"), [1])]
    res = instant_runoff(ballots, options=2, weights={_nf("a"): 3, _nf("b"): 1})
    assert res["winner"] == 0 and res["winner_round"] == 0


@pytest.mark.property
def test_no_ballots_has_no_winner():
    res = instant_runoff([], options=3)
    assert res["winner"] == -1


@pytest.mark.property
def test_revote_highest_seq_wins():
    ballots = [_rb(_nf("a"), [1], seq=0), _rb(_nf("a"), [0], seq=1), _rb(_nf("b"), [0])]
    res = instant_runoff(ballots, options=2)
    assert res["winner"] == 0  # a's later (seq 1) ballot ranks 0 first -> 0 has both


@pytest.mark.property
@pytest.mark.parametrize("bad", [(), (0, 0), (0, -1)])
def test_invalid_ranking_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        RankedBallot(scope=SCOPE, poll_id=POLL, ranking=bad, voter="pls1x", scope_nullifier=_nf("a"))


@pytest.mark.property
def test_out_of_range_option_rejected_by_tally():
    with pytest.raises(ValueError):
        instant_runoff([_rb(_nf("a"), [5])], options=3)  # option 5 not in 0..2


@pytest.mark.property
def test_certified_ranked_result():
    authority_priv, _ = crypto.generate_keypair()
    poll = VbankPoll(authority_priv, SCOPE).define(
        Poll(scope=SCOPE, poll_id=POLL, options=3, opens_at=0, closes_at=10))
    ballots = [_rb(_nf("a"), [0]), _rb(_nf("b"), [0]), _rb(_nf("c"), [1])]
    att = certify_ranked_result(poll.record, ballots, authority_priv)
    assert att.verify(author_field="authority")
    assert att.record["kind"] == RANKED_RESULT_KIND
    assert att.record["winner"] == 0
    assert att.record["poll_cid"]
    assert audit_ranked_result(att, poll.record, ballots)
    # a different ballot set must not verify
    assert not verify_ranked_result(att.record, poll.record, ballots[:1])


@pytest.mark.property
def test_certified_ranked_excludes_out_of_window():
    authority_priv, _ = crypto.generate_keypair()
    poll = VbankPoll(authority_priv, SCOPE).define(
        Poll(scope=SCOPE, poll_id=POLL, options=2, opens_at=0, closes_at=10))
    ballots = [_rb(_nf("a"), [0]), dict(_rb(_nf("b"), [1]), cast_at=999)]  # b out of window
    att = certify_ranked_result(poll.record, ballots, authority_priv)
    assert att.record["voters"] == 1 and att.record["winner"] == 0


@pytest.mark.property
def test_ranked_certify_skips_malformed_ballot_not_fatal():
    authority_priv, _ = crypto.generate_keypair()
    poll = VbankPoll(authority_priv, SCOPE).define(
        Poll(scope=SCOPE, poll_id=POLL, options=3, opens_at=0, closes_at=10))
    good = _rb(_nf("a"), [0])
    bad = _rb(_nf("b"), [5])   # option 5 out of range -> skipped, not fatal
    att = certify_ranked_result(poll.record, [good, bad], authority_priv)
    assert att.record["voters"] == 1 and att.record["winner"] == 0


@pytest.mark.property
def test_only_authority_certifies_ranked():
    authority_priv, _ = crypto.generate_keypair()
    other_priv, _ = crypto.generate_keypair()
    poll = VbankPoll(authority_priv, SCOPE).define(
        Poll(scope=SCOPE, poll_id=POLL, options=2, opens_at=0, closes_at=10))
    with pytest.raises(ValueError):
        certify_ranked_result(poll.record, [_rb(_nf("a"), [0])], other_priv)


@pytest.mark.property
def test_emit_gated_and_collect_from_web():
    priv, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    nf = _nf("voter")
    ticket = PersonhoodTicket(scope=SCOPE, scope_nullifier=nf, pairwise_did=f"did:pls:{addr}",
                              holder_pairwise=addr, not_before=0, not_after=10)
    ballot = RankedBallot(scope=SCOPE, poll_id=POLL, ranking=(2, 0, 1), voter=addr,
                          scope_nullifier=nf, cast_at=1)
    att = emit_ranked_ballot(ballot, ticket, priv)
    assert att.verify(author_field="actor")
    web = Web()
    web.weave(att.record)
    web.weave({"kind": "knowledge-item", "scope": SCOPE, "poll_id": POLL})  # noise
    assert len(collect_ranked_ballots(web, SCOPE, POLL)) == 1
