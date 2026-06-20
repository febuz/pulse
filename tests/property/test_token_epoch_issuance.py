"""R1 — epoch-bound issuance: the Pulse heartbeat governs the PLS money supply.

Binds ``token.mint.Treasury`` to ``core.pulse.Pulse`` so that minting is capped per
epoch (``EmissionPolicy.epoch_cap``). These proofs pin:

  * a per-epoch supply ceiling: within one epoch, total mint never exceeds the cap;
  * the ceiling REFILLS when the heartbeat crosses into a new epoch;
  * backward compatibility: a treasury without a Pulse / without epoch_cap mints
    exactly as before (the epoch bound is a pure superset);
  * BYTE-IDENTITY: the issuance/coinbase canonical record is unchanged by the new
    epoch field (it is audit-only, excluded from to_record()).

Time is injected (timestamps), so epochs are deterministic.
"""

import pytest

from knitweb.core import crypto
from knitweb.core.pulse import Pulse
from knitweb.ledger.node import AccountNode
from knitweb.pouw.job import SynapticCompileJob, WorkProof, execute
from knitweb.token.mint import NATIVE, EmissionPolicy, Issuance, Treasury


def _job(tag="a"):
    orig_priv, orig_pub = crypto.generate_keypair()
    asset = {
        "origintrail_id": 7,
        "originator": "Acme-" + tag,
        "linked_sources": [{"type": "IFRS_File", "url": "https://ifrs.org/" + tag}],
    }
    return SynapticCompileJob(asset=asset, originator_pub=orig_pub), orig_priv


def _verified_job(tag):
    job, priv = _job(tag)
    return job, execute(job, priv)


# --- per-epoch supply ceiling ---------------------------------------------- #


def test_epoch_cap_limits_mint_within_one_epoch():
    """Within one epoch, cumulative mint never exceeds epoch_cap, even with demand."""
    # rate 1/1 (mint == escrow), epoch_cap 15. Two jobs of escrow 10 in the SAME
    # epoch want 20 minted, but the epoch ceiling holds it to 15.
    pulse = Pulse(interval_s=100, genesis_ts=0)
    t = Treasury(EmissionPolicy(rate_num=1, rate_den=1, epoch_cap=15), pulse=pulse)
    consumer = AccountNode(genesis_balances={"PLS": 100})

    # ts=10 and ts=20 are both epoch 0 (interval 100).
    j1, p1 = _verified_job("1")
    i1 = t.reward_verified_work(consumer, AccountNode(), 10, j1, p1, timestamp=10)
    j2, p2 = _verified_job("2")
    i2 = t.reward_verified_work(consumer, AccountNode(), 10, j2, p2, timestamp=20)

    assert i1.amount == 10           # first fits under the cap
    assert i2.amount == 5            # second clamped to remaining 15-10 = 5
    assert t.epoch_minted(0) == 15   # epoch ceiling reached exactly
    assert t.total_minted == 15


def test_epoch_ceiling_refills_next_epoch():
    """Crossing into a new epoch refills the per-epoch mint budget."""
    pulse = Pulse(interval_s=100, genesis_ts=0)
    t = Treasury(EmissionPolicy(rate_num=1, rate_den=1, epoch_cap=10), pulse=pulse)
    consumer = AccountNode(genesis_balances={"PLS": 100})

    j1, p1 = _verified_job("1")
    t.reward_verified_work(consumer, AccountNode(), 10, j1, p1, timestamp=10)   # epoch 0
    assert t.epoch_minted(0) == 10

    j2, p2 = _verified_job("2")
    i2 = t.reward_verified_work(consumer, AccountNode(), 10, j2, p2, timestamp=150)  # epoch 1
    assert i2.amount == 10           # fresh budget in epoch 1
    assert i2.epoch == 1
    assert t.epoch_minted(1) == 10
    assert t.total_minted == 20


def test_epoch_cap_zero_mints_nothing_but_still_settles():
    """epoch_cap=0 mints nothing, yet escrow still settles (conservation holds)."""
    pulse = Pulse(interval_s=100, genesis_ts=0)
    t = Treasury(EmissionPolicy(rate_num=1, rate_den=1, epoch_cap=0), pulse=pulse)
    consumer = AccountNode(genesis_balances={"PLS": 100})
    worker = AccountNode()
    j, p = _verified_job("z")
    before = consumer.balance(NATIVE) + worker.balance(NATIVE)
    iss = t.reward_verified_work(consumer, worker, 10, j, p, timestamp=10)
    assert iss.amount == 0
    assert t.total_minted == 0
    assert worker.balance(NATIVE) == 10                      # escrow settled
    assert consumer.balance(NATIVE) + worker.balance(NATIVE) == before  # conserved


# --- backward compatibility (pure superset) -------------------------------- #


def test_no_pulse_behaves_exactly_as_before():
    """Without a Pulse, epoch accounting is inert and minting is unchanged."""
    t = Treasury(EmissionPolicy(rate_num=1, rate_den=2))  # no pulse, no epoch_cap
    consumer = AccountNode(genesis_balances={"PLS": 100})
    j, p = _verified_job("n")
    iss = t.reward_verified_work(consumer, AccountNode(), 10, j, p, timestamp=10)
    assert iss.amount == 5            # escrow/2, unbounded by epoch
    assert iss.epoch is None
    assert t.epoch_minted(0) == 0


def test_pulse_without_epoch_cap_is_unbounded():
    """A Pulse but no epoch_cap records the epoch but does not clamp."""
    pulse = Pulse(interval_s=100, genesis_ts=0)
    t = Treasury(EmissionPolicy(rate_num=1, rate_den=1), pulse=pulse)  # no epoch_cap
    consumer = AccountNode(genesis_balances={"PLS": 100})
    j, p = _verified_job("u")
    iss = t.reward_verified_work(consumer, AccountNode(), 10, j, p, timestamp=10)
    assert iss.amount == 10           # not clamped
    assert iss.epoch == 0             # epoch still recorded for audit


def test_epoch_cap_validation():
    with pytest.raises(TypeError):
        EmissionPolicy(epoch_cap=True)
    with pytest.raises(ValueError):
        EmissionPolicy(epoch_cap=-1)


# --- byte-identity guard --------------------------------------------------- #


def test_issuance_canonical_record_unchanged_by_epoch_field():
    """The audit-only ``epoch`` field must NOT alter the issuance canonical bytes/CID."""
    base = dict(worker="pls1xyz", amount=5, escrow=10, job_digest="dd", timestamp=1)
    no_epoch = Issuance(**base)
    with_epoch = Issuance(**base, epoch=7)
    # epoch differs, but the canonical record + CID are byte-identical.
    assert with_epoch.to_record() == no_epoch.to_record()
    assert with_epoch.cid == no_epoch.cid
    assert "epoch" not in with_epoch.to_record()
