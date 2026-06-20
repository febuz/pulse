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

    # Normalize inputs, keep deterministic for all runtime edge cases.
    reputation_signal = float(max(0, reputation))
    recency_signal = max(0.0, min(1.0, float(recency)))
    pouw_signal = max(0.0, float(pouw_score))

    # Deterministic additive blend.  Keep relation weights in a small, byte-stable
    # range for compact signatures and predictable ordering.
    blended = (0.6 * reputation_signal) + (60.0 * recency_signal) + (0.7 * pouw_signal)
    quantized = int(blended)
    if quantized < 0:
        return 0
    if quantized > max_weight:
        return max_weight
    return quantized
