"""Verifier committee selection — who re-executes a given job, unpredictably yet verifiably.

The PoUW pipeline is: a worker submits a proof → a *committee* of verifiers re-executes sampled
slices (``pouw/challenge.py``, sized by ``pouw/sampling.py``) → their verdicts aggregate into a
decision (``pouw/quorum.py``) → settle or slash (``pouw/dispute.py``). The one stage with no
implementation is the first: **which** verifiers check the job. Left to the worker it is trivially
gamed — pick friendly verifiers and fraud is rubber-stamped — so, exactly like Algorand's VRF
committees and Ethereum's attestation committees, the committee must be:

  * **unpredictable** before a fresh seed exists (the worker can't pre-select its jury), and
  * **verifiable** afterward (anyone recomputes the same committee from the seed and the eligible
    set, so the assignment is auditable and equivocation-free).

This module is that selection, built from the same SHA-256 counter-stream primitive
``challenge.sample_indices`` already uses for block sampling — a fresh ``seed`` (e.g. derived from
the worker's committed Merkle root plus a per-epoch beacon, so it cannot exist at commit time)
deterministically draws ``k`` **distinct** verifiers from the eligible set, in a reproducible
selection order. The worker is excluded from its own jury (you cannot verify your own work).

Selection is order-independent in the *input* (the eligible set is canonicalised by sorting before
indexing), so two honest nodes holding the same eligible set and seed always agree. Pure, integer/
hash only; no floats, no canonical/signed-record changes.
"""

from __future__ import annotations

from typing import List, Optional

from ..core import crypto

__all__ = ["select_committee"]


def select_committee(
    seed: bytes,
    eligible: List[str],
    k: int,
    *,
    exclude: Optional[str] = None,
) -> List[str]:
    """Deterministically draw ``min(k, |pool|)`` distinct verifiers for a job.

    ``seed`` is the unpredictable per-job randomness (bytes). ``eligible`` is the verifier pool
    (peer ids / PLS addresses); it is de-duplicated and **sorted** before indexing, so the result
    depends only on the *set* and the seed, never on input order. ``exclude`` (typically the
    worker) is removed from the pool — you cannot sit on your own jury. The return is the committee
    in deterministic **selection order** (draw order), so callers may assign priority by position.
    """
    if not isinstance(seed, (bytes, bytearray)):
        raise TypeError("seed must be bytes")
    if not seed:
        raise ValueError("seed must be non-empty (an unpredictable per-job value)")
    if not isinstance(k, int) or isinstance(k, bool):
        raise TypeError("k must be int")
    if k < 0:
        raise ValueError("k must be >= 0")
    for p in eligible:
        if not isinstance(p, str) or not p:
            raise TypeError("every eligible verifier must be a non-empty str")

    pool = sorted(set(eligible) - ({exclude} if exclude is not None else set()))
    want = min(k, len(pool))
    if want == 0:
        return []

    n = len(pool)
    chosen: List[str] = []
    seen: set[int] = set()
    counter = 0
    while len(chosen) < want:
        h = crypto.sha256(bytes(seed) + counter.to_bytes(8, "big"))
        idx = int.from_bytes(h[:8], "big") % n
        counter += 1
        if idx not in seen:
            seen.add(idx)
            chosen.append(pool[idx])
    return chosen
