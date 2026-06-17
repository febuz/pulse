"""Proofs for proof-of-useful-work: honest work verifies + settles; fraud earns nothing."""

import pytest

from knitweb.core import crypto
from knitweb.ledger.node import AccountNode
from knitweb.pouw.escrow import settle_on_verify
from knitweb.pouw.job import SynapticCompileJob, WorkProof, execute, verify


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


@pytest.mark.property
def test_honest_work_verifies():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    assert verify(job, proof)


@pytest.mark.property
def test_tampered_bytecode_fails_verification():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    bad = WorkProof(bytecode=proof.bytecode[:-1] + bytes([proof.bytecode[-1] ^ 1]),
                    signature=proof.signature, digest=proof.digest)
    assert not verify(job, bad)            # digest mismatch / recompile mismatch


@pytest.mark.property
def test_wrong_originator_key_fails():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    _, other_pub = crypto.generate_keypair()
    spoofed = SynapticCompileJob(asset=job.asset, originator_pub=other_pub)
    assert not verify(spoofed, proof)      # signature won't match the claimed key


@pytest.mark.property
def test_settlement_pays_only_for_verified_work():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    consumer = AccountNode(genesis_balances={"PLS": 10})
    worker = AccountNode()

    paid = settle_on_verify(consumer, worker, 2, job, proof, timestamp=1)
    assert paid
    assert consumer.balance("PLS") == 8 and worker.balance("PLS") == 2


@pytest.mark.property
def test_settlement_refuses_fraudulent_proof():
    job, orig_priv = _job()
    proof = execute(job, orig_priv)
    forged = WorkProof(bytecode=proof.bytecode, signature="deadbeef", digest=proof.digest)
    consumer = AccountNode(genesis_balances={"PLS": 10})
    worker = AccountNode()

    paid = settle_on_verify(consumer, worker, 2, job, forged, timestamp=1)
    assert not paid
    assert consumer.balance("PLS") == 10 and worker.balance("PLS") == 0  # no payment
