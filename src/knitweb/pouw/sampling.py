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

from fractions import Fraction

__all__ = [
    "miss_probability",
    "catch_probability",
    "required_samples",
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
