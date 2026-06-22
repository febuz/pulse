"""Proofs for liquid (delegated) voting: chain resolution, direct override, cycles, weights."""

import pytest

from knitweb.core import crypto
from knitweb.knitwebs.vbank import (
    BALLOT_KIND,
    DELEGATION_KIND,
    LIQUID_RESULT_KIND,
    Delegation,
    Poll,
    VbankPoll,
    audit_liquid_result,
    certify_liquid_result,
    delegation_map,
    emit_delegation,
    resolve_liquid,
    verify_liquid_result,
)
from knitweb.personhood.gate import PersonhoodTicket

SCOPE = "vbank"
POLL = "p1"


def _nf(name: str) -> str:
    return crypto.sha256(name.encode()).hex()


def _ballot(nullifier: str, choice: int, seq: int = 0) -> dict:
    return {
        "kind": BALLOT_KIND, "scope": SCOPE, "poll_id": POLL, "choice": choice,
        "actor": "pls1" + nullifier[:16], "scope_nullifier": nullifier, "seq": seq, "cast_at": 1,
    }


def _deleg(delegator_nf: str, delegate_nf: str, seq: int = 0) -> dict:
    return {
        "kind": DELEGATION_KIND, "scope": SCOPE, "poll_id": POLL,
        "actor": "pls1" + delegator_nf[:16], "scope_nullifier": delegator_nf,
        "delegate_nullifier": delegate_nf, "seq": seq,
    }


# ── resolve_liquid algorithm ─────────────────────────────────────────────────

@pytest.mark.property
def test_direct_votes_only():
    assert resolve_liquid({_nf("a"): 0, _nf("b"): 1}, {}) == {0: 1, 1: 1}


@pytest.mark.property
def test_simple_delegation_flows_weight():
    # a delegates to b; b votes 1 -> both count for 1
    assert resolve_liquid({_nf("b"): 1}, {_nf("a"): _nf("b")}) == {1: 2}


@pytest.mark.property
def test_direct_vote_overrides_own_delegation():
    # a votes 0 AND delegated to b (who votes 1): a's own vote wins
    out = resolve_liquid({_nf("a"): 0, _nf("b"): 1}, {_nf("a"): _nf("b")})
    assert out == {0: 1, 1: 1}


@pytest.mark.property
def test_transitive_delegation():
    # a -> b -> c, c votes 2
    out = resolve_liquid({_nf("c"): 2}, {_nf("a"): _nf("b"), _nf("b"): _nf("c")})
    assert out == {2: 3}


@pytest.mark.property
def test_cycle_abstains():
    assert resolve_liquid({}, {_nf("a"): _nf("b"), _nf("b"): _nf("a")}) == {}


@pytest.mark.property
def test_dead_end_abstains():
    # a delegates to b, but b never votes or delegates
    assert resolve_liquid({}, {_nf("a"): _nf("b")}) == {}


@pytest.mark.property
def test_weighted_liquid():
    direct = {_nf("a"): 0, _nf("b"): 1}
    deleg = {_nf("c"): _nf("a")}  # c delegates to a
    weights = {_nf("a"): 5, _nf("b"): 3, _nf("c"): 2}
    assert resolve_liquid(direct, deleg, weights) == {0: 7, 1: 3}  # a:5 + c:2 -> 0; b:3 -> 1


# ── dedup + record layer ─────────────────────────────────────────────────────

@pytest.mark.property
def test_delegation_map_highest_seq_wins():
    records = [_deleg(_nf("a"), _nf("b"), seq=0), _deleg(_nf("a"), _nf("c"), seq=1)]
    assert delegation_map(records) == {_nf("a"): _nf("c")}


@pytest.mark.property
def test_cannot_delegate_to_self():
    with pytest.raises(ValueError):
        Delegation(scope=SCOPE, poll_id=POLL, delegator="pls1x",
                   delegator_nullifier=_nf("a"), delegate_nullifier=_nf("a"))


@pytest.mark.property
def test_emit_delegation_is_gated_and_signed():
    priv, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    nf = _nf("delegator")
    ticket = PersonhoodTicket(scope=SCOPE, scope_nullifier=nf, pairwise_did=f"did:pls:{addr}",
                              holder_pairwise=addr, not_before=0, not_after=10)
    delegation = Delegation(scope=SCOPE, poll_id=POLL, delegator=addr,
                            delegator_nullifier=nf, delegate_nullifier=_nf("delegate"))
    att = emit_delegation(delegation, ticket, priv)
    assert att.verify(author_field="actor")
    assert att.record["kind"] == DELEGATION_KIND
    # a ticket for a different nullifier cannot authorise this delegation
    bad_ticket = PersonhoodTicket(scope=SCOPE, scope_nullifier=_nf("someone-else"),
                                  pairwise_did=f"did:pls:{addr}", holder_pairwise=addr,
                                  not_before=0, not_after=10)
    with pytest.raises(ValueError):
        emit_delegation(delegation, bad_ticket, priv)


def _poll(authority_priv):
    return VbankPoll(authority_priv, SCOPE).define(
        Poll(scope=SCOPE, poll_id=POLL, options=3, opens_at=0, closes_at=10))


@pytest.mark.property
def test_certified_liquid_result():
    authority_priv, _ = crypto.generate_keypair()
    poll = _poll(authority_priv)
    ballots = [_ballot(_nf("b"), 1), _ballot(_nf("c"), 2)]
    delegations = [_deleg(_nf("a"), _nf("b"))]  # a -> b (votes 1)
    att = certify_liquid_result(poll.record, ballots, delegations, authority_priv)
    assert att.verify(author_field="authority")
    assert att.record["kind"] == LIQUID_RESULT_KIND
    assert att.record["results"] == [[1, 2], [2, 1]]   # b + a delegated -> 1:2 ; c -> 2:1
    assert att.record["winner"] == 1 and att.record["winner_votes"] == 2
    assert att.record["direct_voters"] == 2 and att.record["delegations"] == 1
    assert audit_liquid_result(att, poll.record, ballots, delegations)
    # a different delegation set must not verify against this certified result
    assert not verify_liquid_result(att.record, poll.record, ballots, [])


@pytest.mark.property
def test_cross_poll_or_scope_delegation_does_not_inflate():
    authority_priv, _ = crypto.generate_keypair()
    poll = _poll(authority_priv)  # scope SCOPE, poll_id POLL
    ballots = [_ballot(_nf("a"), 0)]
    foreign_poll = dict(_deleg(_nf("b"), _nf("a")), poll_id="OTHER-POLL")
    foreign_scope = dict(_deleg(_nf("c"), _nf("a")), scope="OTHER-SCOPE")
    att = certify_liquid_result(poll.record, ballots, [foreign_poll, foreign_scope], authority_priv)
    assert att.record["results"] == [[0, 1]]   # foreign delegations ignored (not [[0,3]])
    assert att.record["delegations"] == 0
    assert verify_liquid_result(att.record, poll.record, ballots, [foreign_poll, foreign_scope])


@pytest.mark.property
def test_verify_liquid_result_rejects_non_dict():
    authority_priv, _ = crypto.generate_keypair()
    poll = _poll(authority_priv)
    assert verify_liquid_result([1, 2], poll.record, [], []) is False
    assert verify_liquid_result({"kind": LIQUID_RESULT_KIND}, "nope", [], []) is False


@pytest.mark.property
def test_certified_liquid_result_commits_audit_roots():
    authority_priv, _ = crypto.generate_keypair()
    poll = _poll(authority_priv)
    ballots = [_ballot(_nf("a"), 0)]
    delegations = [_deleg(_nf("b"), _nf("a"))]
    att = certify_liquid_result(poll.record, ballots, delegations, authority_priv)
    assert crypto.is_valid_hex(att.record["ballot_root"], 32)
    assert crypto.is_valid_hex(att.record["delegation_root"], 32)
    # the ballot_root commits to the counted set: adding a counted ballot changes it
    att2 = certify_liquid_result(poll.record, ballots + [_ballot(_nf("c"), 1)],
                                 delegations, authority_priv)
    assert att2.record["ballot_root"] != att.record["ballot_root"]


@pytest.mark.property
def test_only_authority_certifies_liquid_result():
    authority_priv, _ = crypto.generate_keypair()
    other_priv, _ = crypto.generate_keypair()
    poll = _poll(authority_priv)
    with pytest.raises(ValueError):
        certify_liquid_result(poll.record, [_ballot(_nf("b"), 1)], [], other_priv)


@pytest.mark.property
def test_liquid_result_excludes_out_of_window_ballots():
    authority_priv, _ = crypto.generate_keypair()
    poll = _poll(authority_priv)  # window [0, 10)
    ballots = [_ballot(_nf("b"), 1), dict(_ballot(_nf("c"), 2), cast_at=999)]  # c is out of window
    att = certify_liquid_result(poll.record, ballots, [], authority_priv)
    assert att.record["results"] == [[1, 1]]
    assert att.record["direct_voters"] == 1
