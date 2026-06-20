"""Deterministic Relation Weights for Interpretation Distillation.

Canonical relation weighting is the only place where `reputation`, `recency` and
`pouw_score` are combined for non-deterministic read-path selection. The same
formula must be used everywhere so two spiders distill the same candidate slice to
identical weights and byte-identical signed bundles.
"""

from __future__ import annotations

__all__ = ["quantize_weight"]


def quantize_weight(
    reputation: int,
    recency: float,
    pouw_score: float,
    *,
    max_weight: int = 255,
) -> int:
    """Quantize candidate quality signals into a deterministic non-negative int.

    Parameters
    ----------
    reputation
        Off-chain/metadata reputation score (already bounded by the caller).
    recency
        Freshness signal: 1.0 is most recent, 0.0 is stale.
    pouw_score
        Work-quality signal from PoUW feedback context.
    max_weight
        Upper bound for emitted relation weight.

    Returns
    -------
    int
        A deterministic, bounded (0..max_weight) weight.
    """
    if not isinstance(reputation, int) or isinstance(reputation, bool):
        raise TypeError("reputation must be an int")
    if not isinstance(recency, (int, float)):
        raise TypeError("recency must be a number")
    if not isinstance(pouw_score, (int, float)):
        raise TypeError("pouw_score must be a number")
    if not isinstance(max_weight, int) or max_weight < 0 or isinstance(max_weight, bool):
        raise TypeError("max_weight must be a non-negative int")

    if not 0 <= max_weight <= 1024:
        raise ValueError("max_weight must be at most 1024")

    # Boundary normalization: collapse the (possibly float) signals to integer
    # thousandths *once*, at the edge, so the blend itself is pure integer
    # arithmetic.  ``int(x * 1000)`` truncates toward zero and is the only place a
    # float value is touched; there is no float literal, no ``float()`` call, and
    # no true division on the value path below.  Rationale: a float-free blend is
    # cross-machine deterministic, which the signed bundle / Knit CID depend on.
    reputation_units = max(0, reputation)  # already an integer score
    recency_milli = int(recency * 1000)  # thousandths of the freshness signal
    if recency_milli < 0:
        recency_milli = 0
    if recency_milli > 1000:
        recency_milli = 1000
    pouw_milli = int(pouw_score * 1000)  # thousandths of the work-quality signal
    if pouw_milli < 0:
        pouw_milli = 0

    # Deterministic additive blend in fixed point.  This is the integer-only
    # equivalent of the historical ``0.6*rep + 60*recency + 0.7*pouw`` derivation.
    # recency_milli and pouw_milli carry the *thousandths* of their signals, so the
    # blend is ``(6000*rep + 600*recency_milli + 7*pouw_milli) // 10000``: each
    # coefficient times its signal, over a common divisor of 10000, floored.  Keep
    # relation weights in a small, byte-stable range for compact signatures.
    blended = (6000 * reputation_units + 600 * recency_milli + 7 * pouw_milli) // 10000
    if blended < 0:
        return 0
    if blended > max_weight:
        return max_weight
    return blended
