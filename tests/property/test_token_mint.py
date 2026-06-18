"""Proofs for native PLS issuance: demand-gated, bounded, no-premine minting.

The economic loop: verified useful work settles the consumer's escrow to the worker
AND mints a bounded reward to the worker. Fraud mints nothing. Issuance never exceeds
the escrow consumed nor the supply cap, is replay-proof, and keeps supply accounting exact.
"""

import pytest

from knitweb.core import crypto
from knitweb.ledger.braid import BraidError
from knitweb.ledger.node import AccountNode
from knitweb.pouw.job import SynapticCompileJob, WorkProof, execute
from knitweb.token.mint import NATIVE, EmissionPolicy, Treasury


def _job():
    orig_priv, orig_pub = crypto.generate_keypair()
    asset = {
        "origintrail_id": 7,
        "originator": "Acme",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "News_Article", "url": "https://news.example/a"},
        ],
    }
    return SynapticCompileJob(asset=asset, originator_pub=orig_pub), orig_priv


def _total(*nodes) -> int:
    return sum(n.balance(NATIVE) for n in nodes)


@pytest.mark.property
def test_no_premine():
    # A fresh treasury has issued nothing; native PLS only exists via work.
    t = Treasury()
    assert t.total_minted == 0 and t.issuances == []


@pytest.mark.property
def test_policy_rejects_bool_parameters():
    with pytest.raises(TypeError):
        EmissionPolicy(rate_num=True)
    with pytest.raises(TypeError):
        EmissionPolicy(rate_den=False)
    with pytest.raises(TypeError):
        EmissionPolicy(max_supply=True)


@pytest.mark.property
def test_verified_work_settles_escrow_and_mints_bounded_reward():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    consumer = AccountNode(genesis_balances={"PLS": 100})
    worker = AccountNode()
    t = Treasury(EmissionPolicy(rate_num=1, rate_den=2))  # reward = escrow/2

    before = _total(consumer, worker)
    iss = t.reward_verified_work(consumer, worker, escrow=10, job=job, proof=proof, timestamp=1)

    assert iss is not None and iss.amount == 5            # 10 * 1/2
    assert consumer.balance(NATIVE) == 90                 # escrow settled out
    assert worker.balance(NATIVE) == 15                   # 10 escrow + 5 minted
    assert t.total_minted == 5
    assert _total(consumer, worker) == before + 5         # supply grew by exactly the mint
    assert worker.braid.validate()                        # coinbase keeps the braid valid


@pytest.mark.property
def test_fraudulent_proof_mints_and_settles_nothing():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    forged = WorkProof(bytecode=proof.bytecode, signature="deadbeef", digest=proof.digest)
    consumer = AccountNode(genesis_balances={"PLS": 100})
    worker = AccountNode()
    t = Treasury()

    iss = t.reward_verified_work(consumer, worker, 10, job, forged, timestamp=1)
    assert iss is None
    assert consumer.balance(NATIVE) == 100 and worker.balance(NATIVE) == 0
    assert t.total_minted == 0


@pytest.mark.property
def test_failed_escrow_transfer_does_not_burn_verified_work():
    # A valid proof must not be marked "rewarded" until settlement/mint succeeds.
    # Otherwise an underfunded consumer could permanently burn future rewards for
    # the same work digest.
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    underfunded = AccountNode(genesis_balances={"PLS": 5})
    worker = AccountNode()
    t = Treasury()

    with pytest.raises(ValueError, match="below escrow"):
        t.reward_verified_work(underfunded, worker, 10, job, proof, timestamp=1)
    assert t.total_minted == 0 and t.issuances == []

    funded = AccountNode(genesis_balances={"PLS": 100})
    iss = t.reward_verified_work(funded, worker, 10, job, proof, timestamp=2)
    assert iss is not None and iss.amount == 5
    assert t.total_minted == 5


@pytest.mark.property
def test_bool_escrow_is_rejected():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    consumer = AccountNode(genesis_balances={"PLS": 100})
    worker = AccountNode()
    with pytest.raises(TypeError):
        Treasury().reward_verified_work(consumer, worker, True, job, proof, timestamp=1)


@pytest.mark.property
def test_mint_never_exceeds_escrow_demand():
    # Even with a >100% rate, the reward is clamped to the escrow (demand bound).
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    consumer = AccountNode(genesis_balances={"PLS": 100})
    worker = AccountNode()
    t = Treasury(EmissionPolicy(rate_num=10, rate_den=1))  # would be 10x escrow

    iss = t.reward_verified_work(consumer, worker, escrow=7, job=job, proof=proof, timestamp=1)
    assert iss.amount == 7                                 # clamped to escrow, not 70
    assert t.total_minted == 7


@pytest.mark.property
def test_max_supply_cap_is_respected():
    # Each reward is a DISTINCT piece of work (distinct asset ⇒ distinct bytecode
    # digest), so the anti-replay guard doesn't fire; the cap is what limits emission.
    def fresh(asset_id: int):
        priv, pub = crypto.generate_keypair()
        asset = {"origintrail_id": asset_id, "originator": f"Org{asset_id}",
                 "linked_sources": [{"type": "IFRS_File", "url": "https://ifrs.org"}]}
        job = SynapticCompileJob(asset=asset, originator_pub=pub)
        return job, execute(job, priv)
    t = Treasury(EmissionPolicy(rate_num=1, rate_den=1, max_supply=8))  # reward=escrow, cap 8

    j1, p1 = fresh(101); c1 = AccountNode(genesis_balances={"PLS": 100}); w1 = AccountNode()
    i1 = t.reward_verified_work(c1, w1, 5, j1, p1, timestamp=1)
    assert i1.amount == 5 and t.total_minted == 5
    j2, p2 = fresh(102); c2 = AccountNode(genesis_balances={"PLS": 100}); w2 = AccountNode()
    i2 = t.reward_verified_work(c2, w2, 5, j2, p2, timestamp=2)
    assert i2.amount == 3 and t.total_minted == 8         # capped: only 3 left to mint
    j3, p3 = fresh(103); c3 = AccountNode(genesis_balances={"PLS": 100}); w3 = AccountNode()
    i3 = t.reward_verified_work(c3, w3, 5, j3, p3, timestamp=3)
    assert i3.amount == 0 and t.total_minted == 8         # settled, but emission exhausted
    assert w3.balance(NATIVE) == 5                        # escrow still settled


@pytest.mark.property
def test_same_issuance_cannot_be_double_minted():
    # Replaying the identical coinbase (same issuance CID) is rejected by the braid
    # spent-knit guard — no double-mint.
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    consumer = AccountNode(genesis_balances={"PLS": 100})
    worker = AccountNode()
    t = Treasury()
    iss = t.reward_verified_work(consumer, worker, 10, job, proof, timestamp=1)
    assert iss is not None
    # Re-applying the same coinbase fiber must fail (issuance CID already spent).
    with pytest.raises(BraidError):
        t._coinbase(worker, iss.amount, iss)


@pytest.mark.property
def test_same_work_proof_cannot_be_rewarded_twice():
    # Anti-replay / no-infinite-mint: resubmitting the same verified proof (even at a
    # different timestamp) rewards nothing the second time, so a colluding
    # consumer+worker can't cycle escrow to mint unboundedly.
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    consumer = AccountNode(genesis_balances={"PLS": 1000})
    worker = AccountNode()
    t = Treasury(EmissionPolicy(rate_num=1, rate_den=2))

    first = t.reward_verified_work(consumer, worker, 10, job, proof, timestamp=1)
    assert first is not None and first.amount == 5 and t.total_minted == 5
    c_after, w_after = consumer.balance(NATIVE), worker.balance(NATIVE)

    for ts in (2, 3):
        assert t.reward_verified_work(consumer, worker, 10, job, proof, timestamp=ts) is None
    assert t.total_minted == 5                    # no extra issuance
    assert consumer.balance(NATIVE) == c_after    # no extra escrow settled
    assert worker.balance(NATIVE) == w_after


@pytest.mark.property
def test_zero_escrow_verified_work_settles_and_mints_nothing():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    consumer = AccountNode(genesis_balances={"PLS": 100})
    worker = AccountNode()
    t = Treasury()
    iss = t.reward_verified_work(consumer, worker, escrow=0, job=job, proof=proof, timestamp=1)
    assert iss is not None and iss.amount == 0
    assert consumer.balance(NATIVE) == 100 and worker.balance(NATIVE) == 0
    assert t.total_minted == 0
