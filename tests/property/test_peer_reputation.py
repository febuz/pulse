"""Proofs for peer reputation / ban-score accounting.

Detection (feed conflicts, equivocation, bad proofs) needs a consequence: misbehavior accrues an
integer score and a peer at/above the threshold is banned. Provable offenses (equivocation, feed
conflict) are one-shot bans. Deterministic and integer-only — decay is explicit, never wall-clock.
"""

import pytest

from knitweb.p2p.reputation import (
    DEFAULT_BAN_THRESHOLD,
    Offense,
    PeerReputation,
)

A = "did:key:peerA"
B = "did:key:peerB"


# ── 1. Accrual and banning ───────────────────────────────────────────────────

def test_unknown_peer_is_clean():
    r = PeerReputation()
    assert r.score(A) == 0
    assert not r.is_banned(A)
    assert r.banned() == []


def test_penalties_accumulate_until_ban():
    r = PeerReputation(ban_threshold=100)
    assert not r.penalize(A, Offense.MALFORMED_FRAME)   # 10
    assert not r.penalize(A, Offense.INVALID_SIGNATURE)  # +50 = 60
    assert r.score(A) == 60 and not r.is_banned(A)
    banned_now = r.penalize(A, Offense.INVALID_SIGNATURE)  # +50 = 110 >= 100
    assert banned_now and r.is_banned(A)
    assert r.banned() == [A]


def test_provable_offenses_are_one_shot_bans():
    for off in (Offense.EQUIVOCATION, Offense.FEED_CONFLICT):
        r = PeerReputation()
        assert r.penalize(A, off) is True            # single provable offense = instant ban
        assert r.is_banned(A)


def test_offense_weights_are_positive_ints_with_graded_severity():
    # 1. Every weight is a plain int (not bool) — penalize() reads offense.value
    #    straight onto the integer score path with no _require_int guard.
    for o in Offense:
        assert type(o.value) is int
        assert not isinstance(o.value, bool)
    # 2. Every weight is strictly positive.
    for o in Offense:
        assert o.value > 0
    # 3. The provable one-shot-ban offenses are anchored to the threshold constant.
    assert (
        Offense.EQUIVOCATION.value
        == Offense.FEED_CONFLICT.value
        == DEFAULT_BAN_THRESHOLD
        == 100
    )
    # 4. Graded severity ordering by .value.
    assert (
        Offense.MALFORMED_FRAME.value
        < Offense.OVERSIZED_FRAME.value
        == Offense.UNSOLICITED_MESSAGE.value
        < Offense.INVALID_SIGNATURE.value
        == Offense.STALE_OR_FORGED_PROOF.value
        < Offense.FEED_CONFLICT.value
    )


def test_explicit_integer_points():
    r = PeerReputation(ban_threshold=50)
    assert not r.penalize(A, 49)
    assert r.penalize(A, 1)                           # 50 >= 50
    assert r.is_banned(A)


# ── 2. Per-peer isolation ────────────────────────────────────────────────────

def test_peers_are_independent():
    r = PeerReputation()
    r.penalize(A, Offense.EQUIVOCATION)              # A banned
    r.penalize(B, Offense.MALFORMED_FRAME)           # B barely dinged
    assert r.is_banned(A) and not r.is_banned(B)
    assert r.banned() == [A]


def test_banned_list_is_sorted_deterministic():
    r = PeerReputation(ban_threshold=10)
    for p in ("did:key:z", "did:key:a", "did:key:m"):
        r.penalize(p, Offense.MALFORMED_FRAME)       # exactly 10 -> banned
    assert r.banned() == ["did:key:a", "did:key:m", "did:key:z"]


# ── 3. Decay / rehabilitation (deterministic, no wall-clock) ─────────────────

def test_decay_rehabilitates_below_threshold():
    r = PeerReputation(ban_threshold=100)
    r.penalize(A, 110)
    assert r.is_banned(A)
    r.decay(A, 20)                                   # 110 -> 90
    assert r.score(A) == 90 and not r.is_banned(A)   # un-banned once below threshold


def test_decay_floors_at_zero():
    r = PeerReputation()
    r.penalize(A, 30)
    r.decay(A, 999)
    assert r.score(A) == 0


def test_decay_all_applies_to_every_peer():
    r = PeerReputation(ban_threshold=100)
    r.penalize(A, 100)
    r.penalize(B, 40)
    r.decay_all(50)
    assert r.score(A) == 50 and r.score(B) == 0
    assert not r.is_banned(A)


def test_forgive_clears_the_record():
    r = PeerReputation()
    r.penalize(A, Offense.EQUIVOCATION)
    r.forgive(A)
    assert r.score(A) == 0 and not r.is_banned(A)


# ── 4. Threshold + validation guards ─────────────────────────────────────────

def test_default_threshold_constant():
    assert DEFAULT_BAN_THRESHOLD == 100
    assert PeerReputation().ban_threshold == 100


def test_custom_threshold():
    r = PeerReputation(ban_threshold=200)
    assert r.penalize(A, Offense.EQUIVOCATION) is False  # 100 < 200, not yet banned
    assert not r.is_banned(A)


def test_invalid_threshold_rejected():
    with pytest.raises(ValueError):
        PeerReputation(ban_threshold=0)
    with pytest.raises(TypeError):
        PeerReputation(ban_threshold=True)


def test_penalize_rejects_nonpositive_points_and_bad_peer():
    r = PeerReputation()
    with pytest.raises(ValueError):
        r.penalize(A, 0)
    with pytest.raises(ValueError):
        r.penalize(A, -5)
    with pytest.raises(TypeError):
        r.penalize("", Offense.MALFORMED_FRAME)
    with pytest.raises(TypeError):
        r.penalize(A, True)                          # bool is not valid points


def test_decay_rejects_negative():
    r = PeerReputation()
    with pytest.raises(ValueError):
        r.decay(A, -1)
