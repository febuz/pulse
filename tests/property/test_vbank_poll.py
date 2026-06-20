"""Proofs for the vBank poll lifecycle: signed definitions + authority-certified results."""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import attest, verify_record
from knitweb.fabric.web import Web
from knitweb.knitwebs.vbank import (
    BALLOT_KIND,
    POLL_KIND,
    RESULT_KIND,
    Poll,
    VbankPoll,
    audit_result,
    verify_result,
)

SCOPE = "vbank"
POLL_ID = "referendum-1"


def _authority():
    priv, _ = crypto.generate_keypair()
    return priv, VbankPoll(priv, SCOPE)


def _nf(i: int) -> str:
    return crypto.sha256(f"voter-{i}".encode()).hex()


def _ballot(nullifier: str, choice: int, seq: int = 0, cast_at: int = 1500) -> dict:
    # default cast_at 1500 is inside the test poll window [1000, 2000)
    return {
        "kind": BALLOT_KIND, "scope": SCOPE, "poll_id": POLL_ID, "choice": choice,
        "actor": "pls1" + nullifier[:16], "scope_nullifier": nullifier, "seq": seq,
        "cast_at": cast_at,
    }


def _poll(authority: VbankPoll, options: int = 3, quorum: int = 0):
    return authority.define(Poll(scope=SCOPE, poll_id=POLL_ID, options=options,
                                 opens_at=1000, closes_at=2000, quorum=quorum))


@pytest.mark.property
@pytest.mark.parametrize("bad", [
    {"options": 1, "opens_at": 0, "closes_at": 10},   # too few options
    {"options": 3, "opens_at": 10, "closes_at": 10},  # window not positive
    {"options": 3, "opens_at": 10, "closes_at": 5},   # closes before opens
    {"options": 3, "opens_at": 0, "closes_at": 10, "quorum": -1},  # negative quorum
])
def test_invalid_poll_definitions_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        Poll(scope=SCOPE, poll_id=POLL_ID, **bad)


@pytest.mark.property
def test_poll_definition_is_signed_and_well_formed():
    priv, authority = _authority()
    att = _poll(authority)
    assert att.verify(author_field="authority")
    assert att.record["kind"] == POLL_KIND
    assert att.record["options"] == 3
    assert att.record["authority"] == authority.authority


@pytest.mark.property
def test_certified_result_counts_and_links_to_definition():
    priv, authority = _authority()
    poll_att = _poll(authority, options=3)
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 2), _ballot(_nf(2), 0)]
    res = authority.certify_result(poll_att.record, ballots)
    assert res.verify(author_field="authority")
    assert res.record["kind"] == RESULT_KIND
    assert res.record["total_voters"] == 3
    assert res.record["results"] == [[0, 2], [2, 1]]
    assert res.record["poll_cid"] == canonical.cid(poll_att.record)


@pytest.mark.property
def test_out_of_range_choice_is_excluded_not_fatal():
    # An out-of-range ballot must be SKIPPED, not abort certification of the whole poll (griefing).
    priv, authority = _authority()
    poll_att = _poll(authority, options=3)
    res = authority.certify_result(poll_att.record, [_ballot(_nf(0), 1), _ballot(_nf(1), 3)])
    assert res.record["total_voters"] == 1        # the choice-3 ballot is dropped, not fatal
    assert res.record["results"] == [[1, 1]]


@pytest.mark.property
def test_only_defining_authority_can_certify():
    _, authority_a = _authority()
    _, authority_b = _authority()
    poll_att = _poll(authority_a, options=2)
    with pytest.raises(ValueError):
        authority_b.certify_result(poll_att.record, [_ballot(_nf(0), 1)])


@pytest.mark.property
def test_result_is_deterministic_and_order_independent():
    priv, authority = _authority()
    poll_att = _poll(authority, options=3)
    ballots = [_ballot(_nf(i), i % 3) for i in range(6)]
    a = authority.certify_result(poll_att.record, ballots)
    b = authority.certify_result(poll_att.record, list(reversed(ballots)))
    assert a.cid == b.cid  # content id is independent of ballot order


@pytest.mark.property
def test_ballots_outside_voting_window_are_excluded():
    priv, authority = _authority()
    poll_att = _poll(authority, options=3)  # window [1000, 2000)
    ballots = [
        _ballot(_nf(0), 0, cast_at=1500),   # in window  -> counts
        _ballot(_nf(1), 1, cast_at=999),    # before opens_at -> excluded
        _ballot(_nf(2), 2, cast_at=2000),   # == closes_at (exclusive) -> excluded
        _ballot(_nf(3), 0, cast_at=2500),   # after close -> excluded
    ]
    res = authority.certify_result(poll_att.record, ballots)
    assert res.record["total_voters"] == 1
    assert res.record["results"] == [[0, 1]]


@pytest.mark.property
def test_out_of_window_revote_does_not_override_in_window_vote():
    priv, authority = _authority()
    poll_att = _poll(authority, options=3)
    # same voter: in-window seq0 choice0, then a LATER (higher-seq) but out-of-window choice2
    ballots = [
        _ballot(_nf(0), 0, seq=0, cast_at=1500),
        _ballot(_nf(0), 2, seq=1, cast_at=2500),  # higher seq but outside window -> ignored
    ]
    res = authority.certify_result(poll_att.record, ballots)
    assert res.record["total_voters"] == 1
    assert res.record["results"] == [[0, 1]]  # the in-window choice 0 stands


@pytest.mark.property
def test_quorum_met_and_not_met():
    priv, authority = _authority()
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 1), _ballot(_nf(2), 0)]  # 3 voters

    met = authority.certify_result(_poll(authority, options=3, quorum=2).record, ballots)
    assert met.record["quorum"] == 2 and met.record["quorum_met"] is True

    not_met = authority.certify_result(_poll(authority, options=3, quorum=5).record, ballots)
    assert not_met.record["quorum"] == 5 and not_met.record["quorum_met"] is False


@pytest.mark.property
def test_plurality_winner_and_tie_break():
    priv, authority = _authority()
    poll = _poll(authority, options=3)
    # choices: two for option 0, one for option 2 -> winner 0
    res = authority.certify_result(poll.record, [_ballot(_nf(0), 0), _ballot(_nf(1), 0), _ballot(_nf(2), 2)])
    assert res.record["winner"] == 0 and res.record["winner_votes"] == 2 and res.record["tie"] is False


@pytest.mark.property
def test_tie_resolves_to_smallest_option_and_flags_tie():
    priv, authority = _authority()
    poll = _poll(authority, options=3)
    res = authority.certify_result(poll.record, [_ballot(_nf(0), 2), _ballot(_nf(1), 1)])  # 1 vs 1
    assert res.record["winner"] == 1          # smallest option among the tied leaders
    assert res.record["winner_votes"] == 1
    assert res.record["tie"] is True


@pytest.mark.property
def test_no_votes_has_no_winner():
    priv, authority = _authority()
    poll = _poll(authority, options=3, quorum=1)
    res = authority.certify_result(poll.record, [])
    assert res.record["winner"] == -1
    assert res.record["winner_votes"] == 0
    assert res.record["tie"] is False
    assert res.record["quorum_met"] is False   # 0 voters < quorum 1


@pytest.mark.property
def test_verify_result_rejects_non_dict_inputs():
    priv, authority = _authority()
    poll = _poll(authority, options=3)
    res = authority.certify_result(poll.record, [_ballot(_nf(0), 0)])
    assert verify_result(res.record, poll.record, [_ballot(_nf(0), 0)])  # sanity: honest passes
    assert verify_result([1, 2, 3], poll.record, []) is False            # non-dict result
    assert verify_result(res.record, "not-a-dict", []) is False          # non-dict poll


@pytest.mark.property
def test_independent_audit_of_a_certified_result():
    priv, authority = _authority()
    poll = _poll(authority, options=3, quorum=2)
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 0), _ballot(_nf(2), 2)]
    result = authority.certify_result(poll.record, ballots)
    # an auditor with the poll, the ballots, and the signed result can confirm everything
    assert verify_result(result.record, poll.record, ballots)
    assert audit_result(result, poll.record, ballots)


@pytest.mark.property
def test_audit_fails_if_ballot_set_differs():
    priv, authority = _authority()
    poll = _poll(authority, options=3)
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 0)]
    result = authority.certify_result(poll.record, ballots)
    # auditor presented with an extra (or missing) ballot must reject the recomputation
    assert not verify_result(result.record, poll.record, ballots + [_ballot(_nf(2), 1)])
    assert not verify_result(result.record, poll.record, ballots[:1])


@pytest.mark.property
def test_audit_fails_if_result_record_tampered():
    priv, authority = _authority()
    poll = _poll(authority, options=3)
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 0), _ballot(_nf(2), 1)]
    result = authority.certify_result(poll.record, ballots)
    forged = dict(result.record, winner=1)  # lie about the winner
    assert not verify_result(forged, poll.record, ballots)


@pytest.mark.property
def test_audit_fails_on_broken_signature():
    from knitweb.fabric.attest import Attestation
    priv, authority = _authority()
    poll = _poll(authority, options=2)
    ballots = [_ballot(_nf(0), 1), _ballot(_nf(1), 1), _ballot(_nf(2), 0)]  # winner is 1
    result = authority.certify_result(poll.record, ballots)
    assert result.record["winner"] == 1
    # the signature is over the real record; the forged record (winner 0) won't verify
    forged = Attestation(record=dict(result.record, winner=0),
                         author_pub=result.author_pub, sig=result.sig)
    assert not audit_result(forged, poll.record, ballots)


@pytest.mark.property
def test_audit_fails_if_result_authority_not_poll_authority():
    _, authority_a = _authority()
    _, authority_b = _authority()
    poll = _poll(authority_a, options=2)
    ballots = [_ballot(_nf(0), 1)]
    # authority B certifies a result claiming B as authority; it does not match poll A's authority
    result_b = _result_for(authority_b, poll.record, ballots)
    assert not verify_result(result_b.record, poll.record, ballots)


def _result_for(authority: VbankPoll, poll_record: dict, ballots: list) -> object:
    # build a signed result whose 'authority' is this (non-defining) authority, bypassing the
    # defining-authority guard, to prove verify_result still rejects the authority mismatch
    from knitweb.fabric.attest import attest
    from knitweb.knitwebs.vbank.poll import _result_record
    record = _result_record(poll_record, ballots, authority.authority)
    return attest(record, authority._priv, author_field="authority")


@pytest.mark.property
def test_weighted_result_sums_fixed_point_weights():
    priv, authority = _authority()
    poll = _poll(authority, options=3)
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 1), _ballot(_nf(2), 0)]
    weights = {_nf(0): 5, _nf(1): 3, _nf(2): 2}
    res = authority.certify_result(poll.record, ballots, weights)
    assert res.record["weighted"] is True
    assert res.record["results"] == [[0, 7], [1, 3]]      # option 0: 5+2, option 1: 3
    assert res.record["winner"] == 0 and res.record["winner_votes"] == 7
    assert res.record["total_weight"] == 10
    assert res.record["weight_root"] != ""
    assert verify_result(res.record, poll.record, ballots, weights)
    assert audit_result(res, poll.record, ballots, weights)


@pytest.mark.property
def test_weighted_audit_fails_with_wrong_weights():
    priv, authority = _authority()
    poll = _poll(authority, options=3)
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 1)]
    res = authority.certify_result(poll.record, ballots, {_nf(0): 4, _nf(1): 1})
    assert not verify_result(res.record, poll.record, ballots, {_nf(0): 1, _nf(1): 1})
    assert not verify_result(res.record, poll.record, ballots)  # unweighted != weighted result


@pytest.mark.property
def test_unweighted_result_has_empty_weight_root():
    priv, authority = _authority()
    poll = _poll(authority, options=2)
    res = authority.certify_result(poll.record, [_ballot(_nf(0), 0), _ballot(_nf(1), 1)])
    assert res.record["weighted"] is False
    assert res.record["weight_root"] == ""
    assert res.record["total_weight"] == 2  # one per voter


@pytest.mark.property
def test_voter_absent_from_weight_map_weighs_zero():
    priv, authority = _authority()
    poll = _poll(authority, options=3)
    ballots = [_ballot(_nf(0), 0), _ballot(_nf(1), 1)]
    res = authority.certify_result(poll.record, ballots, {_nf(0): 3})  # nf1 omitted -> 0
    assert res.record["results"] == [[0, 3], [1, 0]]
    assert res.record["winner"] == 0 and res.record["total_weight"] == 3


@pytest.mark.property
def test_negative_weight_rejected():
    priv, authority = _authority()
    poll = _poll(authority, options=2)
    with pytest.raises(ValueError):
        authority.certify_result(poll.record, [_ballot(_nf(0), 0)], {_nf(0): -1})


@pytest.mark.property
def test_weave_result_into_web():
    priv, authority = _authority()
    poll_att = _poll(authority, options=2)
    web = Web()
    cid, att = authority.weave_result(poll_att.record, [_ballot(_nf(0), 1), _ballot(_nf(1), 0)], web)
    assert att.verify(author_field="authority")
    assert cid == att.cid
