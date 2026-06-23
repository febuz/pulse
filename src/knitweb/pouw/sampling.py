"""Sample-size sizing for PoUW sampled re-execution — how many blocks must a verifier re-run.

``pouw/challenge.py`` lets a verifier re-execute ``k`` sampled output blocks, but leaves the
*choice* of ``k`` to the caller. The soundness of sampled re-execution rests entirely on that
number: if a cheating worker corrupts ``corrupt`` of its ``n`` output blocks, a verifier sampling
``k`` distinct blocks **without replacement** misses every corrupt block with probability

    miss(n, corrupt, k) = C(n-corrupt, k) / C(n, k)
                        = ∏_{i=0}^{k-1} (n - corrupt - i) / (n - i)        (hypergeometric)

and therefore *catches* the fraud with probability ``1 - miss``. This module computes that
exactly and inverts it: :func:`required_samples` returns the smallest ``k`` that drives the miss
probability at or below a target (e.g. "catch ≥ 99% of a 1%-corruption"). It is the audit-side
companion to :mod:`knitweb.pouw.collateral`:

  * collateral sizing makes a *detected* fraud unprofitable (slash ≥ value at risk);
  * sample sizing makes an *undetected* fraud improbable (miss ≤ target).

Together a rational worker's expected payoff from cheating is negative.

All arithmetic is exact rational (``fractions.Fraction``) — no floats, no approximation, so the
threshold comparison is exact. This is advisory policy (it sizes the challenge); it touches
neither the canonical/hash path nor any signed record.
"""

from __future__ import annotations

import hashlib
from fractions import Fraction

__all__ = [
    "miss_probability",
    "catch_probability",
    "required_samples",
    # IL-106: job-level audit selection for distill jobs
    "should_audit_job",
    "sample_distill_jobs",
]


def _require_count(name: str, value: int, *, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value})")


def _validate(n: int, corrupt: int, k: int) -> None:
    _require_count("n", n, minimum=1)
    _require_count("corrupt", corrupt, minimum=0)
    _require_count("k", k, minimum=0)
    if corrupt > n:
        raise ValueError(f"corrupt ({corrupt}) cannot exceed n ({n})")
    if k > n:
        raise ValueError(f"k ({k}) cannot exceed n ({n})")


def miss_probability(n: int, corrupt: int, k: int) -> Fraction:
    """Exact probability that ``k`` samples (no replacement) avoid all ``corrupt`` bad blocks.

    ``corrupt == 0`` ⇒ there is nothing to detect, so the miss probability is 1 (a clean worker
    is never "caught"). ``k > n - corrupt`` ⇒ the pigeonhole forces a hit, so it is 0.
    """
    _validate(n, corrupt, k)
    if corrupt == 0:
        return Fraction(1)
    if k > n - corrupt:
        return Fraction(0)
    p = Fraction(1)
    for i in range(k):
        p *= Fraction(n - corrupt - i, n - i)
    return p


def catch_probability(n: int, corrupt: int, k: int) -> Fraction:
    """Exact probability that ``k`` samples hit at least one corrupt block (``1 - miss``)."""
    return Fraction(1) - miss_probability(n, corrupt, k)


def required_samples(n: int, corrupt: int, max_miss: Fraction) -> int:
    """Smallest ``k`` whose :func:`miss_probability` is ``<= max_miss`` (catch ≥ ``1 - max_miss``).

    Size against a *hypothesised* fraud level, so ``corrupt >= 1`` is required. ``max_miss`` is an
    exact :class:`~fractions.Fraction` in ``[0, 1]`` (e.g. ``Fraction(1, 100)`` for ≥99% catch).
    Because miss is monotonically non-increasing in ``k`` and ``miss(n, corrupt, n) = 0``, the
    answer always exists and is ``<= n``.
    """
    _require_count("n", n, minimum=1)
    _require_count("corrupt", corrupt, minimum=1)
    if corrupt > n:
        raise ValueError(f"corrupt ({corrupt}) cannot exceed n ({n})")
    if not isinstance(max_miss, Fraction):
        raise TypeError("max_miss must be a fractions.Fraction (exact, no float)")
    if not (Fraction(0) <= max_miss <= Fraction(1)):
        raise ValueError("max_miss must be in [0, 1]")

    miss = Fraction(1)                      # k = 0: sampled nothing, always misses
    if miss <= max_miss:
        return 0
    for k in range(1, n + 1):
        num = n - corrupt - (k - 1)
        if num <= 0:                        # k > n - corrupt ⇒ a hit is forced, miss = 0
            return k
        miss *= Fraction(num, n - (k - 1))
        if miss <= max_miss:
            return k
    return n                                # miss(n, corrupt, n) == 0 ≤ max_miss (unreachable guard)


# ---------------------------------------------------------------------------
# IL-106 — job-level audit selection for distill PoUW jobs.
#
# Block-level sampling (above) picks WHICH blocks inside one job to re-check.
# Job-level sampling answers a different question: WHICH distill jobs across a
# round should a verifier audit at all?  The mechanism is a deterministic
# hash-based draw so every verifier reading the same (seed, manifest_cid) pair
# reaches the same audit/skip decision without coordination.
# ---------------------------------------------------------------------------

_AUDIT_HASH_BYTES = 8                    # 8 bytes → 64-bit draw, plenty of range


def should_audit_job(seed: bytes, manifest_cid: str, *, rate: Fraction) -> bool:
    """Return True iff this (seed, manifest_cid) pair falls within the audit fraction.

    The draw is deterministic: ``sha256(seed + manifest_cid.encode())`` mapped to
    a uniform integer in ``[0, 2**64)``, then compared to ``rate * 2**64``.  This
    gives every verifier the same yes/no without a shared random source.

    ``rate`` must be a :class:`~fractions.Fraction` in ``[0, 1]`` — no floats touch
    the decision boundary so there are no float-rounding surprises in audit coverage.
    """
    if not isinstance(seed, (bytes, bytearray)):
        raise TypeError("seed must be bytes")
    if not isinstance(manifest_cid, str) or not manifest_cid:
        raise ValueError("manifest_cid must be a non-empty str")
    if not isinstance(rate, Fraction):
        raise TypeError("rate must be a fractions.Fraction (exact, no float)")
    if not (Fraction(0) <= rate <= Fraction(1)):
        raise ValueError("rate must be in [0, 1]")
    if rate == Fraction(0):
        return False
    if rate == Fraction(1):
        return True

    digest = hashlib.sha256(bytes(seed) + manifest_cid.encode()).digest()
    draw = int.from_bytes(digest[:_AUDIT_HASH_BYTES], "big")
    threshold = int(rate * (2 ** (_AUDIT_HASH_BYTES * 8)))
    return draw < threshold


def sample_distill_jobs(
    manifest_cids: list[str],
    rate: Fraction,
    *,
    seed: bytes,
) -> list[str]:
    """Return the subset of ``manifest_cids`` selected for audit at ``rate``.

    Each CID is tested independently via :func:`should_audit_job`; the result is
    deterministic across all verifiers for the same ``(seed, rate)`` round.
    ``seed`` is typically the current epoch/block hash so every verifier uses the
    same global entropy without coordination.

    Returns an empty list when ``rate == 0``; returns ``manifest_cids`` when
    ``rate == 1``.
    """
    if not isinstance(manifest_cids, list):
        raise TypeError("manifest_cids must be a list")
    return [
        cid for cid in manifest_cids
        if should_audit_job(seed, cid, rate=rate)
    ]
