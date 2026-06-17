"""Commit-before-sample challenge protocol with a fresh per-challenge salt.

The sampled-re-execution proof model is only sound if the worker cannot (a)
precompute just the blocks it expects to be challenged on, or (b) swap in
different work *after* learning which blocks are sampled. ``docs/CRYPTO_CORPUS_STUDY.md``
§1 (action 2; Filecoin beacon-seeded challenges, Arweave SPoRA, Livepeer salts)
prescribes: **commit first, sample later, with a fresh salt.**

Protocol (all CPU-deterministic, O(k) verifier cost):

  1. ``commit(blocks)``     — at submit time the worker publishes a domain-separated
                              Merkle ``root`` over its output blocks. The full
                              output is fixed *before* any salt exists.
  2. ``sample_indices(salt, n, k)`` — the verifier draws a fresh random ``salt``
                              and derives k distinct block indices from it; the
                              worker cannot predict them at commit time.
  3. ``respond(blocks, salt, k)`` — the worker reveals the sampled blocks, each
                              with a Merkle membership proof and a salted digest
                              ``sha256(salt || index || block)`` computed now.
  4. ``verify_response(...)`` — the verifier recomputes the indices from the salt,
                              checks each revealed block is a member of the
                              committed root (no work-swap) and that its salted
                              digest matches (the worker still holds real content,
                              computed after the salt — no precompute).

The Merkle tree is built locally with explicit leaf/node domain separation
(second-preimage safety; avoids the CVE-2012-2459 ambiguity); we do not reuse
``crypto.merkle_root`` because it emits no membership proofs and no domain tag.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..core import crypto

__all__ = [
    "Commitment",
    "Reveal",
    "new_salt",
    "commit",
    "sample_indices",
    "respond",
    "verify_response",
]

_LEAF_TAG = b"\x00"
_NODE_TAG = b"\x01"


def _leaf(block: bytes) -> bytes:
    return crypto.sha256(_LEAF_TAG + block)


def _node(left: bytes, right: bytes) -> bytes:
    return crypto.sha256(_NODE_TAG + left + right)


def _build_levels(leaves: list[bytes]) -> list[list[bytes]]:
    """Bottom-up Merkle levels, last (odd) node duplicated. levels[-1] == [root]."""
    levels: list[list[bytes]] = []
    cur = list(leaves)
    while True:
        if len(cur) > 1 and len(cur) % 2 == 1:
            cur = cur + [cur[-1]]          # duplicate the last node on odd levels
        levels.append(cur)
        if len(cur) == 1:
            return levels
        cur = [_node(cur[i], cur[i + 1]) for i in range(0, len(cur), 2)]


def _proof(levels: list[list[bytes]], index: int) -> list[tuple[str, bool]]:
    """Sibling path for ``index`` as (sibling_hex, sibling_is_right) pairs."""
    path: list[tuple[str, bool]] = []
    idx = index
    for level in levels[:-1]:              # every level except the root
        sib = idx ^ 1
        path.append((level[sib].hex(), sib > idx))
        idx //= 2
    return path


def _verify_proof(leaf: bytes, path: list[tuple[str, bool]], root: bytes) -> bool:
    h = leaf
    for sib_hex, sib_is_right in path:
        sib = bytes.fromhex(sib_hex)
        h = _node(h, sib) if sib_is_right else _node(sib, h)
    return h == root


@dataclass(frozen=True)
class Commitment:
    """What a worker publishes at submit time (before any salt exists)."""

    root: bytes
    n: int                                 # number of output blocks committed


@dataclass(frozen=True)
class Reveal:
    """A worker's answer for one sampled block."""

    index: int
    block: bytes
    proof: list[tuple[str, bool]]          # Merkle membership path
    salted: str                            # sha256(salt || index || block) hex


def new_salt(n_bytes: int = 32) -> bytes:
    """A fresh random challenge salt (verifier-chosen, after the commit)."""
    return os.urandom(n_bytes)


def commit(blocks: list[bytes]) -> Commitment:
    """Worker commits to its output blocks. Raises on empty output."""
    if not blocks:
        raise ValueError("cannot commit to an empty output")
    levels = _build_levels([_leaf(b) for b in blocks])
    return Commitment(root=levels[-1][0], n=len(blocks))


def sample_indices(salt: bytes, n: int, k: int) -> list[int]:
    """Derive ``min(k, n)`` distinct block indices deterministically from ``salt``.

    A SHA-256 counter stream over the salt gives an unpredictable-yet-reproducible
    selection: the worker can't know it before the salt, the verifier recomputes
    it exactly. The returned order is the canonical challenge order (see
    :func:`verify_response`).
    """
    if n <= 0 or k <= 0:
        return []
    want = min(k, n)
    out: list[int] = []
    seen: set[int] = set()
    counter = 0
    while len(out) < want:
        h = crypto.sha256(salt + counter.to_bytes(8, "big"))
        idx = int.from_bytes(h[:8], "big") % n
        counter += 1
        if idx not in seen:
            seen.add(idx)
            out.append(idx)
    return out


def _salted_digest(salt: bytes, index: int, block: bytes) -> str:
    return crypto.sha256_hex(salt + index.to_bytes(8, "big") + block)


def respond(blocks: list[bytes], salt: bytes, k: int) -> list[Reveal]:
    """Worker reveals the sampled blocks with membership proofs + salted digests.

    The reveals are returned in :func:`sample_indices` order; keep them in that
    order for :func:`verify_response` (which compares the index list positionally).
    """
    levels = _build_levels([_leaf(b) for b in blocks])
    reveals: list[Reveal] = []
    for i in sample_indices(salt, len(blocks), k):
        reveals.append(
            Reveal(
                index=i,
                block=blocks[i],
                proof=_proof(levels, i),
                salted=_salted_digest(salt, i, blocks[i]),
            )
        )
    return reveals


def verify_response(
    commitment: Commitment,
    salt: bytes,
    k: int,
    reveals: list[Reveal],
) -> bool:
    """Confirm the reveals answer *this* salt against the committed root.

    The reveals MUST be in the exact order produced by :func:`sample_indices` /
    :func:`respond`: the index list is compared positionally, so a reordered (or
    partial) reveal set is rejected. Callers should not sort the reveals.

    Rejects: wrong/missing/reordered sampled indices, a salted digest that doesn't
    match (stale or precomputed answer), or a block that isn't a member of the
    committed Merkle root (retroactive work-swap).
    """
    expected = sample_indices(salt, commitment.n, k)
    if [r.index for r in reveals] != expected:
        return False
    for r in reveals:
        if _salted_digest(salt, r.index, r.block) != r.salted:
            return False
        if not _verify_proof(_leaf(r.block), r.proof, commitment.root):
            return False
    return True
