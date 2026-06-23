"""Property tests for quorum-aware POUW settlement."""

from __future__ import annotations

import pytest

from knitweb.core import crypto
from knitweb.ledger.node import AccountNode
from knitweb.pouw.job import SynapticCompileJob, WorkProof, execute
from knitweb.pouw.quorum import Outcome
from knitweb.pouw.quorum_settlement import proofs_to_verdicts, settle_on_quorum


def _make_job_and_proofs(n: int = 3):
    """Return a job, the originator private key, and ``n`` honest proofs."""
    orig_priv, orig_pub = crypto.generate_keypair()
    asset = {
        "origintrail_id": 7,
        "originator": "Acme",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "News_Article", "url": "https://news.example/a"},
        ],
    }
    job = SynapticCompileJob(asset=asset, originator_pub=orig_pub)
    proofs = [execute(job, orig_priv) for _ in range(n)]
    return job, proofs, orig_priv


@pytest.mark.property
def test_honest_quorum_confirms_and_pays():
    job, proofs, _ = _make_job_and_proofs(n=3)
    consumer = AccountNode(genesis_balances={"PLS": 10})
    worker = AccountNode()

    paid, result = settle_on_quorum(consumer, worker, 3, job, proofs, timestamp=1)

    assert paid is True
    assert result.outcome is Outcome.CONFIRMED
    assert consumer.balance("PLS") == 7
    assert worker.balance("PLS") == 3


@pytest.mark.property
def test_fault_quorum_detects_and_refuses_payment():
    job, proofs, _ = _make_job_and_proofs(n=3)
    # Tamper every proof so all verifiers report mismatch.
    bad_proofs = [
        WorkProof(
            bytecode=p.bytecode[:-1] + bytes([p.bytecode[-1] ^ 1]),
            signature=p.signature,
            digest=p.digest,
        )
        for p in proofs
    ]
    consumer = AccountNode(genesis_balances={"PLS": 10})
    worker = AccountNode()

    paid, result = settle_on_quorum(consumer, worker, 3, job, bad_proofs, timestamp=1)

    assert paid is False
    assert result.outcome is Outcome.DETECTED_FAULT
    assert consumer.balance("PLS") == 10
    assert worker.balance("PLS") == 0


@pytest.mark.property
def test_inconclusive_quorum_keeps_escrow():
    job, proofs, _ = _make_job_and_proofs(n=3)
    # One honest, one tampered, one tampered => 1 confirm / 2 mismatch.
    mixed = [proofs[0]] + [
        WorkProof(
            bytecode=p.bytecode[:-1] + bytes([p.bytecode[-1] ^ 1]),
            signature=p.signature,
            digest=p.digest,
        )
        for p in proofs[1:]
    ]
    consumer = AccountNode(genesis_balances={"PLS": 10})
    worker = AccountNode()

    paid, result = settle_on_quorum(consumer, worker, 3, job, mixed, timestamp=1)

    assert paid is False
    assert result.outcome is Outcome.INCONCLUSIVE
    assert consumer.balance("PLS") == 10
    assert worker.balance("PLS") == 0


@pytest.mark.property
def test_declared_fault_refunds_consumer():
    job, proofs, _ = _make_job_and_proofs(n=3)
    consumer = AccountNode(genesis_balances={"PLS": 10})
    worker = AccountNode()

    paid, result = settle_on_quorum(
        consumer,
        worker,
        3,
        job,
        proofs,
        timestamp=1,
        worker_declared_fault=True,
    )

    assert paid is False
    assert result.outcome is Outcome.DECLARED_FAULT
    assert consumer.balance("PLS") == 10
    assert worker.balance("PLS") == 0


@pytest.mark.property
def test_confirmed_quorum_with_insufficient_balance_returns_inconclusive():
    job, proofs, _ = _make_job_and_proofs(n=3)
    # Consumer only has 2 PLS but the job costs 3; quorum confirms but payment fails.
    consumer = AccountNode(genesis_balances={"PLS": 2})
    worker = AccountNode()

    paid, result = settle_on_quorum(consumer, worker, 3, job, proofs, timestamp=1)

    assert paid is False
    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.releases is False
    assert consumer.balance("PLS") == 2
    assert worker.balance("PLS") == 0


@pytest.mark.property
def test_proofs_to_verdicts_maps_honest_and_fraud():
    job, honest_proofs, _ = _make_job_and_proofs(n=1)
    tampered = WorkProof(
        bytecode=honest_proofs[0].bytecode[:-1] + bytes([honest_proofs[0].bytecode[-1] ^ 1]),
        signature=honest_proofs[0].signature,
        digest=honest_proofs[0].digest,
    )

    verdicts = proofs_to_verdicts(job, [*honest_proofs, tampered])
    assert [v.value for v in verdicts] == ["confirm", "mismatch"]
