"""End-to-end PoUW economic-loop demo — select → verify → quorum → settle.

A runnable acceptance demo tying the proof-of-useful-work primitives and their integrations into
one flow, for the three settlement outcomes the protocol must handle:

  1. an **honest** worker is confirmed by the committee and **released** (paid);
  2. a **cheating** worker is caught by the committee and **slashed**;
  3a worker that **honestly declares a fault** is **refunded** (consumer made whole, no slash).

Each run: the consumer escrows pulses; the worker stakes collateral (sized so fraud can't pay —
``pouw/collateral``); a verifier **committee** is selected unpredictably (``pouw/committee``) and
sized (``pouw/sampling``); each verifier re-executes sampled blocks (``pouw/verify`` over
``pouw/challenge``) and votes; the **quorum** of votes (``pouw/quorum``) drives the dispute ledger
(``pouw/dispute``) to slash / release / refund. The ledger records the integer settlement effects,
which a caller would turn into conservation-preserving Knit/escrow transfers (``pouw/escrow``).

Run:  PYTHONPATH=src python3 examples/pouw_demo.py
"""

from __future__ import annotations

from fractions import Fraction
from typing import List

from knitweb.pouw import challenge, verify
from knitweb.pouw.dispute import DisputeWindowLedger

WORKER = "did:key:worker"
CONSUMER = "did:key:consumer"
ELIGIBLE = [f"did:key:verifier-{i}" for i in range(9)]   # the eligible verifier pool

ESCROW = 100        # pulses the consumer commits
COLLATERAL = 100    # pulses the worker stakes (1:1 — covers the escrow at risk)
N_BLOCKS = 16
COMMITTEE_SIZE = 5
SUBMIT_BEAT = 100


def _blocks(tag: bytes) -> List[bytes]:
    return [tag + i.to_bytes(2, "big") for i in range(N_BLOCKS)]


def _verify_committee(ledger, sid, worker_blocks, recomputed_blocks, *, seed):
    """Select+size the committee, run it, return the verdict stream."""
    commitment = challenge.commit(worker_blocks)
    plan = verify.plan_verification(
        seed, ELIGIBLE, WORKER, N_BLOCKS,
        committee_size=COMMITTEE_SIZE, corrupt_hypothesis=1, max_miss=Fraction(1, 100),
    )
    salts = [f"salt::{v}".encode() for v in plan.committee]
    verdicts = verify.run_committee(commitment, worker_blocks, recomputed_blocks, salts, plan.k)
    return plan, verdicts


def run_demo() -> dict:
    """Run the three scenarios; return a structured summary (used by the acceptance test)."""
    out: dict = {}

    # ── 1. Honest worker → confirmed → released ──────────────────────────────
    led = DisputeWindowLedger(dispute_window=5, release_delay=8, enforce_collateral=True)
    blocks = _blocks(b"ok")
    led.submit("honest", WORKER, CONSUMER, ESCROW, COLLATERAL, SUBMIT_BEAT)
    plan, verdicts = _verify_committee(led, "honest", blocks, blocks, seed=b"seed-honest")
    slashed, _ = led.dispute_by_quorum("honest", verdicts, beat=SUBMIT_BEAT + 1)
    assert not slashed                                   # quorum confirmed → no slash
    released, _ = led.release("honest", beat=SUBMIT_BEAT + 8)
    out["honest"] = {
        "committee": plan.committee, "k": plan.k,
        "all_confirm": all(v.value == "confirm" for v in verdicts),
        "released": released, "status": led.get("honest").status,
    }

    # ── 2. Cheating worker → detected → slashed ──────────────────────────────
    led2 = DisputeWindowLedger(dispute_window=5, release_delay=8, enforce_collateral=True)
    honest_recompute = _blocks(b"ok")
    cheated = _blocks(b"XX")                              # entirely wrong output
    led2.submit("fraud", WORKER, CONSUMER, ESCROW, COLLATERAL, SUBMIT_BEAT)
    _, verdicts2 = _verify_committee(led2, "fraud", cheated, honest_recompute, seed=b"seed-fraud")
    slashed2, reason2 = led2.dispute_by_quorum("fraud", verdicts2, beat=SUBMIT_BEAT + 1)
    out["fraud"] = {
        "all_mismatch": all(v.value == "mismatch" for v in verdicts2),
        "slashed": slashed2, "reason": reason2, "status": led2.get("fraud").status,
        "collateral_slashed": led2.collateral_slashed, "escrow_refunded": led2.escrow_refunded,
    }

    # ── 3. Declared fault → refunded (no slash) ──────────────────────────────
    led3 = DisputeWindowLedger(dispute_window=5, release_delay=8, enforce_collateral=True)
    blocks3 = _blocks(b"ok")
    led3.submit("declared", WORKER, CONSUMER, ESCROW, COLLATERAL, SUBMIT_BEAT)
    _, verdicts3 = _verify_committee(led3, "declared", blocks3, blocks3, seed=b"seed-declared")
    slashed3, _ = led3.dispute_by_quorum(
        "declared", verdicts3, beat=SUBMIT_BEAT + 1, worker_declared_fault=True
    )
    assert not slashed3                                  # owned-up fault is never slashed
    refunded, _ = led3.refund_declared_fault("declared", beat=SUBMIT_BEAT + 1)
    out["declared"] = {
        "refunded": refunded, "status": led3.get("declared").status,
        "collateral_slashed": led3.collateral_slashed,      # 0 — not slashed
        "collateral_returned": led3.collateral_returned,    # stake returned
        "escrow_refunded": led3.escrow_refunded,            # consumer made whole
    }
    return out


def main() -> None:
    r = run_demo()
    print("PoUW end-to-end demo")
    print("=" * 60)
    h = r["honest"]
    print(f"1. HONEST   committee={len(h['committee'])} k={h['k']} "
          f"all_confirm={h['all_confirm']} → {h['status'].upper()} (worker paid)")
    f = r["fraud"]
    print(f"2. FRAUD    all_mismatch={f['all_mismatch']} → {f['status'].upper()} "
          f"(stake {f['collateral_slashed']} burned, escrow {f['escrow_refunded']} refunded)")
    d = r["declared"]
    print(f"3. DECLARED → {d['status'].upper()} (stake {d['collateral_returned']} returned, "
          f"escrow {d['escrow_refunded']} refunded, slashed={d['collateral_slashed']})")
    print("=" * 60)
    print("✓ honest released · fraud slashed · declared refunded — fraud never pays.")


if __name__ == "__main__":
    main()
