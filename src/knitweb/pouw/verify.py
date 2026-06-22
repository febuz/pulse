"""End-to-end PoUW verification flow — select a jury, size the audit, re-execute, vote.

This is the capstone that composes the standalone PoUW primitives into the runnable verification
path; until now each existed in isolation:

  committee.select_committee  (jury)   →  *who* verifies a job, unpredictably yet verifiably
  sampling.required_samples   (audit)  →  *how many* output blocks each verifier re-checks
  challenge.verify_response    (proof) →  the worker's revealed blocks are authentic to its commit
  + re-execution comparison            →  those blocks actually match an honest re-execution
  ⇒ a quorum.Verdict per verifier      →  the exact input quorum.tally / dispute_by_quorum consumes

So this module is the **upstream** half (produce the verdict stream) and
``pouw/dispute.DisputeWindowLedger.dispute_by_quorum`` is the **downstream** half (slash/settle on
it). A verifier's verdict is grounded in re-execution: it independently recomputes the work and
**CONFIRM**s only if every sampled block it checked matches both the worker's signed commitment
*and* its own honest recompute; any divergence — a forged reveal or a wrong block — is a
**MISMATCH**. Sizing ``k`` via ``sampling`` is what makes a sampled (not full) re-check sound: the
bigger the hypothesised corruption, the fewer samples needed to catch it with target confidence.

Deterministic and integer/hash only; no canonical/signed-record changes (it *reads* commitments
and reveals, never mints records).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import List

from . import challenge
from .committee import select_committee
from .quorum import Verdict
from .sampling import required_samples

__all__ = [
    "VerificationPlan",
    "plan_verification",
    "verifier_verdict",
    "run_committee",
]


@dataclass(frozen=True)
class VerificationPlan:
    """Who checks the job (``committee``) and how many blocks each samples (``k``)."""

    committee: List[str]
    k: int


def plan_verification(
    seed: bytes,
    eligible: List[str],
    worker: str,
    n_blocks: int,
    *,
    committee_size: int,
    corrupt_hypothesis: int,
    max_miss: Fraction,
) -> VerificationPlan:
    """Pick the verifier committee (#58) and the per-verifier sample count ``k`` (#55).

    ``k`` is sized to catch a worker that corrupted ``corrupt_hypothesis`` of ``n_blocks`` with
    miss probability ≤ ``max_miss`` (then clamped to ``n_blocks``). The worker is excluded from
    its own jury.
    """
    committee = select_committee(seed, eligible, committee_size, exclude=worker)
    k = min(required_samples(n_blocks, corrupt_hypothesis, max_miss), n_blocks)
    return VerificationPlan(committee=committee, k=k)


def verifier_verdict(
    commitment: challenge.Commitment,
    salt: bytes,
    k: int,
    reveals: List[challenge.Reveal],
    recomputed_blocks: List[bytes],
) -> Verdict:
    """One verifier's verdict over its ``k`` sampled blocks (re-execution spot-check).

    ``CONFIRM`` iff (a) the reveals authentically answer *this* salt against the worker's signed
    ``commitment`` (``challenge.verify_response`` — no work-swap, no precompute) **and** (b) every
    revealed block equals the verifier's own honest re-execution at that index. Otherwise
    ``MISMATCH`` — a forged reveal or a block that differs from a correct recompute.
    """
    if not challenge.verify_response(commitment, salt, k, reveals):
        return Verdict.MISMATCH
    for r in reveals:
        if r.index >= len(recomputed_blocks) or r.block != recomputed_blocks[r.index]:
            return Verdict.MISMATCH
    return Verdict.CONFIRM


def run_committee(
    commitment: challenge.Commitment,
    worker_blocks: List[bytes],
    recomputed_blocks: List[bytes],
    salts: List[bytes],
    k: int,
) -> List[Verdict]:
    """Drive a committee: each verifier draws its own ``salt``, the worker answers, the verifier
    votes. Returns one :class:`~knitweb.pouw.quorum.Verdict` per salt — feed it straight to
    ``quorum.tally`` / ``dispute_by_quorum``.

    Models the protocol honestly: the worker reveals the blocks it actually committed
    (``worker_blocks``) for each verifier's distinct salt, and each verifier compares against its
    independent ``recomputed_blocks``. If the worker cheated (``worker_blocks`` diverges from a
    correct recompute), a verifier whose sample hits a diverging index votes ``MISMATCH`` — which
    is exactly why ``k`` (sized by ``sampling``) governs detection.
    """
    verdicts: List[Verdict] = []
    for salt in salts:
        reveals = challenge.respond(worker_blocks, salt, k)
        verdicts.append(verifier_verdict(commitment, salt, k, reveals, recomputed_blocks))
    return verdicts
