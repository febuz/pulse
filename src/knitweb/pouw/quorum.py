"""k-of-n verifier quorum — aggregate independent challenge verdicts into one decision.

``pouw/challenge.py`` lets a *single* verifier re-execute a sampled slice of a worker's
output and return one boolean verdict (confirm / mismatch). But a single verifier is a
single point of both failure and corruption: a lazy verifier rubber-stamps fraud, and a
malicious one slashes honest work. The DePIN fix (``docs/PROOF_OF_USEFUL_WORK.md`` §4.4,
and the "next increment" note in ``pouw/dispute.py``) is to demand **many independent
verifiers and settle on a quorum** — so no minority of corrupt verifiers can force either
a false confirm or a false slash.

Two ideas live here, both pure / integer-only (no floats, no crypto-path or signed-record
changes — this layer only *counts* verdicts the challenge protocol already produced):

1. **k-of-n supermajority.** The work is ``CONFIRMED`` only when at least ``k`` verifiers
   confirm, and a detected fault (``DETECTED_FAULT`` → slash) is declared only when at
   least ``k`` verifiers report a mismatch. The sound default is the classic BFT
   supermajority ``k = ⌊2n/3⌋ + 1``, which tolerates an adversary of up to
   ``f = ⌊(n-1)/3⌋`` verifiers (the ``n ≥ 3f+1`` bound):

     * the ``f`` adversaries **alone cannot reach ``k``** (``f < k``), so they can neither
       manufacture a confirm nor manufacture a slash; and
     * the ``n - f`` honest verifiers **can always reach ``k``** for the true verdict
       (``n - f ≥ k``).

   Because ``2k > n`` for this ``k``, a confirm-quorum and a mismatch-quorum can never both
   form on the same submission — the verdict is never self-contradictory. (The roadmap's
   loose "~55% confirm" understates this; 55% only survives a vanishing equivocating
   adversary, whereas tolerating a *third* of verifiers genuinely needs the 2/3 quorum.)

2. **Declared-vs-detected fault asymmetry.** A worker that *itself declares* it could not
   complete the job (an honest "I failed" — ``worker_declared_fault=True``) is refunded
   without a slash: you are never slashed for a fault you owned up to (outcome
   ``DECLARED_FAULT``). Slashing is reserved for a *detected* fault — a quorum of
   mismatches against a worker that *claimed success*. This removes the perverse incentive
   to hide a known-bad result and gamble on not being sampled.

When neither quorum is reached (too many abstentions / a split below ``k``) the outcome is
``INCONCLUSIVE`` — settlement waits for more verifiers or a re-sample; nothing is paid or
slashed on a non-quorum.

The result is advisory: the caller drives the matching ``pouw/dispute.py`` action —
``release`` on :attr:`QuorumResult.releases`, ``dispute`` (slash) on
:attr:`QuorumResult.slashes` — so the integer PLS-wei movement stays in the escrow layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Optional

__all__ = [
    "Verdict",
    "Outcome",
    "QuorumResult",
    "default_threshold",
    "max_faulty",
    "tally",
]


class Verdict(Enum):
    """One verifier's independent judgement on a sampled re-execution."""

    CONFIRM = "confirm"      # re-executed the sample; it matched the worker's commitment
    MISMATCH = "mismatch"    # re-executed the sample; it did NOT match (a detected fault)
    ABSTAIN = "abstain"      # did not return in time / declined — counts toward neither quorum


class Outcome(Enum):
    """The aggregate decision over all verifier verdicts."""

    CONFIRMED = "confirmed"            # ≥ k confirms → honest work → release escrow
    DETECTED_FAULT = "detected_fault"  # ≥ k mismatches, worker claimed success → slash
    DECLARED_FAULT = "declared_fault"  # worker self-declared a fault → refund, no slash
    INCONCLUSIVE = "inconclusive"      # neither quorum reached → wait / re-sample


def _require_int(name: str, value: int, *, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value})")


def default_threshold(n: int) -> int:
    """The sound BFT supermajority quorum ``⌊2n/3⌋ + 1`` for ``n`` verifiers.

    This is the smallest ``k`` that (a) a ``⌊(n-1)/3⌋``-sized adversary cannot reach alone
    and (b) the honest remainder can always reach, while guaranteeing ``2k > n`` so confirm
    and mismatch quorums are mutually exclusive.
    """
    _require_int("n", n, minimum=1)
    return (2 * n) // 3 + 1


def max_faulty(n: int) -> int:
    """The largest adversary (in verifiers) the default quorum tolerates: ``⌊(n-1)/3⌋``."""
    _require_int("n", n, minimum=1)
    return (n - 1) // 3


@dataclass(frozen=True)
class QuorumResult:
    """The aggregate verdict plus the tallies that produced it (all integers)."""

    outcome: Outcome
    confirms: int
    mismatches: int
    abstains: int
    n: int
    threshold: int

    @property
    def releases(self) -> bool:
        """True iff escrow should release to the worker (a confirm quorum)."""
        return self.outcome is Outcome.CONFIRMED

    @property
    def slashes(self) -> bool:
        """True iff the worker's collateral should be slashed (a detected-fault quorum)."""
        return self.outcome is Outcome.DETECTED_FAULT

    @property
    def refunds(self) -> bool:
        """True iff escrow returns to the consumer with no slash (a declared fault)."""
        return self.outcome is Outcome.DECLARED_FAULT


def tally(
    verdicts: Iterable[Verdict],
    *,
    worker_declared_fault: bool = False,
    threshold: Optional[int] = None,
) -> QuorumResult:
    """Aggregate independent verifier ``verdicts`` into one :class:`QuorumResult`.

    ``threshold`` overrides the BFT default (must satisfy ``⌈(n+1)/2⌉ ≤ k ≤ n`` so the two
    quorums stay mutually exclusive). ``worker_declared_fault`` short-circuits to
    ``DECLARED_FAULT`` (honest self-report → refund, never a slash) regardless of the
    verifier tally.
    """
    vs: List[Verdict] = list(verdicts)
    n = len(vs)
    if n < 1:
        raise ValueError("need at least one verdict to form a quorum")
    for v in vs:
        if not isinstance(v, Verdict):
            raise TypeError(f"each verdict must be a Verdict, got {type(v).__name__}")
    if not isinstance(worker_declared_fault, bool):
        raise TypeError("worker_declared_fault must be bool")

    k = default_threshold(n) if threshold is None else threshold
    _require_int("threshold", k, minimum=1)
    if k > n:
        raise ValueError(f"threshold {k} exceeds verifier count {n}")
    if 2 * k <= n:
        # Below a strict majority the confirm- and mismatch-quorums could both form,
        # yielding a self-contradictory verdict. Refuse it.
        raise ValueError(
            f"threshold {k} must be a strict majority of {n} (2k > n) to stay unambiguous"
        )

    confirms = sum(1 for v in vs if v is Verdict.CONFIRM)
    mismatches = sum(1 for v in vs if v is Verdict.MISMATCH)
    abstains = sum(1 for v in vs if v is Verdict.ABSTAIN)

    if worker_declared_fault:
        outcome = Outcome.DECLARED_FAULT          # owned-up fault → refund, no slash
    elif confirms >= k:
        outcome = Outcome.CONFIRMED
    elif mismatches >= k:
        outcome = Outcome.DETECTED_FAULT          # quorum caught undeclared fraud → slash
    else:
        outcome = Outcome.INCONCLUSIVE

    return QuorumResult(
        outcome=outcome,
        confirms=confirms,
        mismatches=mismatches,
        abstains=abstains,
        n=n,
        threshold=k,
    )
