"""Tolerance/quantized digests so honest workers aren't slashed for float noise.

Exact-match on raw-float digests is fatal to the proof model: two honest workers
on different GPUs (or BLAS builds) produce bit-different floats for the *same*
job, so byte-equality silently slashes honest work — the #1 existential risk in
``docs/CRYPTO_CORPUS_STUDY.md`` §1 (Chutes/Targon: "raw-float digest equality
breaks under GPU non-determinism").

The fix is to digest a *quantized* view of the output: every value is snapped to
a fixed grid of size ``eps`` (its integer bucket ``floor(value/eps + 0.5)``,
deterministic round-half-up — see ``quantize``) and the
resulting **integers** are hashed via canonical CBOR. Outputs that agree to
within ``eps`` land in the same bucket and produce an identical digest; genuinely
different work lands in different buckets and mismatches. No float ever reaches
the hash (the canonical encoder rejects floats by design), so the digest stays a
float-free deterministic artifact.

This collapses sub-``eps`` noise; values straddling a bucket boundary remain an
inherent edge case (pick ``eps`` well above the expected cross-machine error and
well below the smallest meaningful difference). Where even that is unsafe, fall
back to hardware attestation — quantization is the cheap first line, not a
guarantee for chaotic kernels.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..core import canonical, crypto

__all__ = ["quantize", "tolerance_digest", "digests_agree"]


def quantize(value: float, eps: float) -> int:
    """Snap ``value`` to its integer bucket on a grid of size ``eps``.

    Uses deterministic round-half-up (``floor(x + 0.5)``) rather than Python's
    banker's rounding so the bucket is reproducible across machines. ``eps`` must
    be a positive, finite real.
    """
    if not isinstance(eps, (int, float)) or isinstance(eps, bool):
        raise TypeError("eps must be a real number")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("value must be a real number")
    if not math.isfinite(eps) or eps <= 0:
        raise ValueError("eps must be finite and positive")
    if not math.isfinite(value):
        raise ValueError("value must be finite")
    return math.floor(value / eps + 0.5)


def tolerance_digest(values: Sequence[float], eps: float) -> str:
    """Hex SHA-256 over the quantized integer buckets of ``values``.

    Two outputs whose elements agree to within ``eps`` produce the same digest.
    The hash input is float-free (a CBOR-encoded list of ints), so the digest is
    a deterministic, canonical artifact safe for the settlement path.
    """
    buckets = [quantize(v, eps) for v in values]
    return crypto.sha256_hex(canonical.encode(buckets))


def digests_agree(a: Sequence[float], b: Sequence[float], eps: float) -> bool:
    """True iff ``a`` and ``b`` produce the same tolerance digest at ``eps``."""
    return tolerance_digest(a, eps) == tolerance_digest(b, eps)
