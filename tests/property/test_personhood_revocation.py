"""Proofs for the revocation log: audit feed, signed status commitment, race-freeness."""

import pytest

from knitweb.core import crypto
from knitweb.fabric import feed as feedmod
from knitweb.personhood.records import REASON_ART17_ERASURE
from knitweb.personhood.revocation import (
    RevocationLog,
    StatusCommitment,
    check_non_revocation,
)


def _ptr(i: int) -> str:
    return crypto.sha256(f"revptr-{i}".encode()).hex()


def _log():
    priv, _ = crypto.generate_keypair()
    return RevocationLog(priv, scope="vbank")


@pytest.mark.property
def test_revoke_appends_signed_feed_entries():
    log = _log()
    log.revoke(_ptr(1), revoked_at=10)
    head = log.revoke(_ptr(2), revoked_at=20)
    assert head.verify()
    assert set(log.revoked_pointers()) == {_ptr(1), _ptr(2)}


@pytest.mark.property
def test_status_commitment_is_authority_signed():
    log = _log()
    log.revoke(_ptr(1), revoked_at=10)
    commitment = log.commit_status(epoch=5)
    assert commitment.verify()
    assert commitment.authority == log.authority


@pytest.mark.property
def test_non_revocation_holds_then_fails_after_revoke():
    log = _log()
    log.revoke(_ptr(1), revoked_at=10)
    target = _ptr(99)

    commitment, proof = log.prove_non_revocation(target, epoch=1)
    assert check_non_revocation(commitment, proof)

    # now revoke the target; a fresh commitment must refuse non-revocation
    log.revoke(target, revoked_at=30, reason_code=REASON_ART17_ERASURE)
    assert log.status_tree().contains(target)
    with pytest.raises(KeyError):
        log.prove_non_revocation(target, epoch=2)


@pytest.mark.property
def test_stale_proof_does_not_satisfy_a_later_commitment():
    # Race-elimination: a non-membership proof built at epoch 1 must not validate against
    # a later epoch's commitment whose root changed (someone else was revoked meanwhile).
    log = _log()
    target = _ptr(99)
    _, stale_proof = log.prove_non_revocation(target, epoch=1)

    log.revoke(_ptr(7), revoked_at=40)  # root changes (target still unrevoked)
    later_commitment = log.commit_status(epoch=2)

    assert later_commitment.root != _empty_then(log)  # sanity: tree is non-empty now
    assert not check_non_revocation(later_commitment, stale_proof)
    # the correctly-rebuilt proof at the new epoch still verifies
    fresh_commitment, fresh_proof = log.prove_non_revocation(target, epoch=2)
    assert check_non_revocation(fresh_commitment, fresh_proof)


def _empty_then(log):
    from knitweb.personhood.status_tree import EMPTY_ROOT
    return EMPTY_ROOT


@pytest.mark.property
def test_tampered_commitment_root_fails_verification():
    log = _log()
    log.revoke(_ptr(1), revoked_at=10)
    c = log.commit_status(epoch=3)
    forged = StatusCommitment(
        scope=c.scope, root=crypto.sha256(b"forged").hex(), length=c.length,
        epoch=c.epoch, authority=c.authority, sig=c.sig,
    )
    assert not forged.verify()


@pytest.mark.property
def test_equivocation_is_detectable_on_the_revoke_feed():
    # Two logs under the SAME authority key that revoke different pointers first commit to
    # two different roots at the same (length, fork) -> provable equivocation.
    priv, _ = crypto.generate_keypair()
    a = RevocationLog(priv, scope="vbank")
    b = RevocationLog(priv, scope="vbank")
    head_a = a.revoke(_ptr(1), revoked_at=10)
    head_b = b.revoke(_ptr(2), revoked_at=10)
    assert head_a.length == head_b.length == 1
    assert feedmod.check_conflict(head_a, head_b)


@pytest.mark.property
def test_revocation_pointer_keyed_not_nullifier():
    # The revoke record carries only the random pointer + scope, never a nullifier/identity.
    log = _log()
    log.revoke(_ptr(1), revoked_at=10, reason_code=REASON_ART17_ERASURE)
    entry = log.feed.entries[0]
    assert set(entry.keys()) == {"kind", "verifier", "scope", "revocation_pointer", "revoked_at", "reason_code"}
    assert "scope_nullifier" not in entry
