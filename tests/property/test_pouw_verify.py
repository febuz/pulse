"""Integration proofs: the end-to-end PoUW verification flow (committee+sampling+challenge→Verdict).

A jury is selected (#58), an audit size chosen (#55), and each verifier re-executes sampled blocks
(challenge) to vote CONFIRM/MISMATCH (quorum.Verdict). Honest work confirms; a cheated block is
caught when sampled; forged reveals are rejected. The verdict list is what dispute_by_quorum eats.
"""

from fractions import Fraction

import pytest

from knitweb.pouw import challenge, verify
from knitweb.pouw.quorum import Outcome, Verdict, tally

WORKER = "did:key:worker"
ELIGIBLE = [f"did:key:v{i}" for i in range(12)]


def _blocks(n, tag=b"ok"):
    return [tag + i.to_bytes(2, "big") for i in range(n)]


# ── 1. Planning: committee (#58) + sample size (#55) ─────────────────────────

def test_plan_selects_committee_and_sizes_k():
    plan = verify.plan_verification(
        b"seed", ELIGIBLE, WORKER, n_blocks=100,
        committee_size=5, corrupt_hypothesis=1, max_miss=Fraction(1, 2),
    )
    assert len(plan.committee) == 5
    assert WORKER not in plan.committee
    # corrupt=1 of 100, miss<=1/2  ⇒  k = 50 (see sampling proofs)
    assert plan.k == 50


def test_plan_clamps_k_to_block_count():
    plan = verify.plan_verification(
        b"seed", ELIGIBLE, WORKER, n_blocks=8,
        committee_size=3, corrupt_hypothesis=1, max_miss=Fraction(1, 1000),
    )
    assert plan.k == 8                         # required samples would exceed n → clamped


# ── 2. A single verifier's verdict ───────────────────────────────────────────

def test_honest_work_confirms():
    blocks = _blocks(16)
    com = challenge.commit(blocks)
    salt = b"verifier-salt-1"
    reveals = challenge.respond(blocks, salt, k=8)
    # verifier recomputed the SAME blocks (honest worker)
    assert verify.verifier_verdict(com, salt, 8, reveals, blocks) is Verdict.CONFIRM


def test_cheated_block_is_caught_when_sampled():
    honest = _blocks(16)
    cheated = list(honest)
    cheated[5] = b"TAMPERED"                   # worker's output differs at index 5
    com = challenge.commit(cheated)            # worker commits to its cheated output
    # full sampling (k=n) guarantees index 5 is checked → MISMATCH vs honest recompute
    salt = b"s"
    reveals = challenge.respond(cheated, salt, k=16)
    assert verify.verifier_verdict(com, salt, 16, reveals, honest) is Verdict.MISMATCH


def test_forged_reveal_is_rejected():
    blocks = _blocks(10)
    com = challenge.commit(blocks)
    salt = b"s"
    reveals = challenge.respond(blocks, salt, k=5)
    # tamper a revealed block's bytes so it no longer matches the commitment
    bad = [challenge.Reveal(index=reveals[0].index, block=b"forged",
                            proof=reveals[0].proof, salted=reveals[0].salted)] + reveals[1:]
    assert verify.verifier_verdict(com, salt, 5, bad, blocks) is Verdict.MISMATCH


# ── 3. Full committee run → a verdict stream that drives the quorum ──────────

def test_committee_confirms_honest_worker_and_quorum_agrees():
    blocks = _blocks(20)
    com = challenge.commit(blocks)
    salts = [f"verifier-{i}".encode() for i in range(5)]
    verdicts = verify.run_committee(com, blocks, blocks, salts, k=10)
    assert all(v is Verdict.CONFIRM for v in verdicts)
    # the stream feeds quorum.tally directly:
    assert tally(verdicts).outcome is Outcome.CONFIRMED


def test_committee_catches_heavy_corruption_and_quorum_slashes():
    honest = _blocks(20)
    cheated = list(honest)
    for i in range(10):                        # corrupt half the blocks
        cheated[i] = b"BAD" + i.to_bytes(2, "big")
    com = challenge.commit(cheated)
    salts = [f"verifier-{i}".encode() for i in range(5)]
    # each verifier samples 12 of 20; with half corrupt, every verifier hits a bad block
    verdicts = verify.run_committee(com, cheated, honest, salts, k=12)
    assert all(v is Verdict.MISMATCH for v in verdicts)
    assert tally(verdicts).outcome is Outcome.DETECTED_FAULT   # → dispute_by_quorum would slash


def test_full_audit_always_catches_any_corruption():
    honest = _blocks(12)
    cheated = list(honest)
    cheated[11] = b"x"                          # a single corrupted block
    com = challenge.commit(cheated)
    salts = [b"only-verifier"]
    verdicts = verify.run_committee(com, cheated, honest, salts, k=12)   # k = n → full audit
    assert verdicts == [Verdict.MISMATCH]
