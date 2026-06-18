"""Inclusion proofs over a signed feed — verify a slice without the whole log.

``fabric/feed.py`` signs a feed's *head* (``{feed, root, length, fork}``) so one signature
authenticates the entire Merkle root. But its read path, :func:`feed.verify_entries`,
requires **all** ``length`` entries to recompute that root. For real partial replication
(Hypercore's "download block 7 of a million-entry feed and prove it") a peer must verify a
single entry — or a contiguous range — against the signed root using only an O(log n) sibling
path. This module adds exactly that, the missing piece of backlog **B10**.

The proof matches ``core.crypto.merkle_root`` *exactly* (the function ``feed`` commits with):
a duplicate-last SHA-256 tree over the entry leaves ``leaf(e) = sha256(canonical.encode(e))``,
inner nodes ``sha256(left ‖ right)``, the last node duplicated on odd levels. We re-implement
that construction here (rather than importing internals) so the proof stays correct even if
``crypto.merkle_root``'s surface is reworded elsewhere — the tests cross-check the reconstructed
root against a real ``Feed.root()`` for every index, so any divergence fails loudly.

Security: a proof is self-validating against the **signed** root. A forged sibling path simply
fails to reconstruct ``head.root`` (a second-preimage break would be needed), and the head's
signature is checked first — so a peer trusts a verified entry exactly as much as it trusts the
feed author, with no extra data. Range verification (:func:`verify_range`) is a contiguous run
of inclusion proofs; the bandwidth-optimal shared-path multiproof is a later optimization,
noted in B10.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..core import canonical, crypto
from .feed import FeedHead

__all__ = [
    "InclusionProof",
    "prove_inclusion",
    "verify_inclusion",
    "verify_range",
]


def _leaf(entry: dict) -> bytes:
    """The Merkle leaf for an entry — identical to ``feed._leaf``."""
    return crypto.sha256(canonical.encode(entry))


def _node(left: bytes, right: bytes) -> bytes:
    """Inner node — raw ``sha256(left ‖ right)``, matching ``crypto.merkle_root`` (no tag)."""
    return crypto.sha256(left + right)


def _levels(leaves: List[bytes]) -> List[List[bytes]]:
    """Bottom-up levels with the last node duplicated on odd levels; ``levels[-1] == [root]``.

    Mirrors ``crypto.merkle_root``'s duplicate-last rule so sibling lookups are exact.
    """
    levels: List[List[bytes]] = []
    cur = list(leaves)
    while True:
        if len(cur) > 1 and len(cur) % 2 == 1:
            cur = cur + [cur[-1]]          # duplicate the last node on odd levels
        levels.append(cur)
        if len(cur) == 1:
            return levels
        cur = [_node(cur[i], cur[i + 1]) for i in range(0, len(cur), 2)]


@dataclass(frozen=True)
class InclusionProof:
    """A Merkle sibling path proving an entry sits at ``index`` in a feed of ``length``."""

    index: int
    length: int
    # (sibling_hex, sibling_is_right): the sibling hash and whether it sits to our right.
    path: List[Tuple[str, bool]]


def prove_inclusion(entries: List[dict], index: int) -> InclusionProof:
    """Build the inclusion proof for ``entries[index]`` (seeder side — has the full log)."""
    n = len(entries)
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError("index must be int")
    if not 0 <= index < n:
        raise IndexError(f"index {index} out of range for {n} entries")
    levels = _levels([_leaf(e) for e in entries])
    path: List[Tuple[str, bool]] = []
    idx = index
    for level in levels[:-1]:              # every level except the root
        sib = idx ^ 1
        path.append((level[sib].hex(), sib > idx))
        idx //= 2
    return InclusionProof(index=index, length=n, path=path)


def _reconstruct_root(leaf: bytes, path: List[Tuple[str, bool]]) -> bytes:
    h = leaf
    for sib_hex, sib_is_right in path:
        sib = bytes.fromhex(sib_hex)
        h = _node(h, sib) if sib_is_right else _node(sib, h)
    return h


def verify_inclusion(head: FeedHead, entry: dict, proof: InclusionProof) -> bool:
    """True iff ``entry`` is the committed entry at ``proof.index`` of the signed ``head``.

    Checks, in order: the head's signature; the proof's claimed length matches the head;
    the index is in range; and the sibling path reconstructs exactly ``head.root``. Any
    failure ⇒ reject (a peer never trusts an unverified or out-of-range entry).
    """
    if not head.verify():
        return False
    if proof.length != head.length:
        return False
    if not 0 <= proof.index < head.length:
        return False
    return _reconstruct_root(_leaf(entry), proof.path).hex() == head.root


def verify_range(
    head: FeedHead,
    start: int,
    entries: List[dict],
    proofs: List[InclusionProof],
) -> bool:
    """Verify a **contiguous** run ``entries`` begins at ``start`` and is all committed.

    Each ``entries[i]`` must verify at index ``start + i`` against ``head`` with ``proofs[i]``.
    This is partial-range replication built on per-entry inclusion (the shared-path multiproof
    that collapses common siblings is a later bandwidth optimization). An empty range within a
    valid signed head is vacuously true.
    """
    if not isinstance(start, int) or isinstance(start, bool):
        raise TypeError("start must be int")
    if len(entries) != len(proofs):
        raise ValueError("entries and proofs must be the same length")
    if start < 0 or start + len(entries) > head.length:
        return False
    if not head.verify():
        return False
    for i, (entry, proof) in enumerate(zip(entries, proofs)):
        if proof.index != start + i:
            return False
        if not verify_inclusion(head, entry, proof):
            return False
    return True
