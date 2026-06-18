"""Shared-path range multiproof over a signed feed — O(log n) hashes for a whole range.

``fabric/feed_proof.py`` (B10, first half) verifies entries against a signed ``FeedHead``,
but a contiguous range of ``m`` entries costs ``m`` independent sibling paths —
``O(m · log n)`` hashes. This module is the bandwidth-optimal half: one **multiproof** that
authenticates an entire contiguous range ``[start, start+count)`` with the *shared* sibling
hashes only, ``O(count + log n)``.

The win comes from the range being **contiguous**. As the known node set propagates up the
tree, it stays a contiguous interval, so on every level at most the **two boundary siblings**
(left of the interval, right of the interval) fall outside the known set — every interior
pair is fully known and therefore derivable. So a multiproof carries ~``2·⌈log₂ n⌉`` hashes
regardless of how wide the range is.

Tree shape matches ``core.crypto.merkle_root`` *exactly* (what ``feed`` commits with): a
duplicate-last SHA-256 tree, leaves ``sha256(canonical.encode(entry))``, inner nodes
``sha256(left ‖ right)``, the last node duplicated on odd levels. The prover and verifier
derive the *same* sibling order from the range bounds + feed length alone (no indices on the
wire), so the proof is just an ordered list of hashes. As in ``feed_proof.py`` the
construction is re-implemented locally (robust to #49's ``crypto.py`` edits) and pinned by
tests cross-checking the reconstructed root against a real ``Feed.root()``.

Security is identical to single-entry inclusion: a forged sibling list simply fails to
reconstruct the **signed** ``head.root`` (a second-preimage break would be needed), and the
head signature is checked first. (Once ``feed_proof.py`` lands, the shared ``_leaf``/``_node``/
level helpers here should be consolidated with it — noted in the PR.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set

from ..core import canonical, crypto
from .feed import FeedHead

__all__ = [
    "RangeMultiProof",
    "prove_range",
    "verify_range_multiproof",
]


def _leaf(entry: dict) -> bytes:
    return crypto.sha256(canonical.encode(entry))


def _node(left: bytes, right: bytes) -> bytes:
    return crypto.sha256(left + right)


def _levels(leaves: List[bytes]) -> List[List[bytes]]:
    """Bottom-up levels, last node duplicated on odd levels; ``levels[-1] == [root]``."""
    levels: List[List[bytes]] = []
    cur = list(leaves)
    while True:
        if len(cur) > 1 and len(cur) % 2 == 1:
            cur = cur + [cur[-1]]
        levels.append(cur)
        if len(cur) == 1:
            return levels
        cur = [_node(cur[i], cur[i + 1]) for i in range(0, len(cur), 2)]


@dataclass(frozen=True)
class RangeMultiProof:
    """Authenticates the contiguous range ``[start, start+count)`` of a feed of ``length``.

    ``siblings`` is the ordered list of out-of-range sibling hashes (hex), consumed bottom-up,
    left-boundary-before-right within each level — exactly the order both prover and verifier
    derive from ``(start, count, length)``.
    """

    start: int
    count: int
    length: int
    siblings: List[str]


def _require_index(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int")


def prove_range(entries: List[dict], start: int, count: int) -> RangeMultiProof:
    """Build a shared-path multiproof for ``entries[start:start+count]`` (seeder side)."""
    n = len(entries)
    _require_index("start", start)
    _require_index("count", count)
    if count <= 0:
        raise ValueError("count must be >= 1")
    if start < 0 or start + count > n:
        raise IndexError(f"range [{start},{start + count}) out of bounds for {n} entries")

    levels = _levels([_leaf(e) for e in entries])
    known: Set[int] = set(range(start, start + count))
    siblings: List[str] = []
    for level in levels[:-1]:                      # every level except the root
        handled: Set[int] = set()
        nxt: Set[int] = set()
        for i in sorted(known):                    # increasing index → deterministic order
            if i in handled:
                continue
            s = i ^ 1
            handled.add(i)
            handled.add(s)
            if s not in known:
                siblings.append(level[s].hex())    # out-of-range sibling → must be carried
            nxt.add(i // 2)
        known = nxt
    return RangeMultiProof(start=start, count=count, length=n, siblings=siblings)


def verify_range_multiproof(
    head: FeedHead, entries: List[dict], proof: RangeMultiProof
) -> bool:
    """True iff ``entries`` are exactly the committed range ``[start, start+count)`` of ``head``.

    Reconstructs the root from the range leaves + the carried siblings (deriving the same
    sibling order from the range bounds and ``head.length``) and compares it to the signed
    ``head.root``. Checks the head signature, the length/bounds, and that every carried
    sibling is consumed (no extra). Any mismatch ⇒ reject.
    """
    if not head.verify():
        return False
    if proof.length != head.length:
        return False
    if proof.count != len(entries):
        return False
    if proof.count <= 0:
        return False
    if proof.start < 0 or proof.start + proof.count > head.length:
        return False

    nodes: Dict[int, bytes] = {proof.start + i: _leaf(e) for i, e in enumerate(entries)}
    known: Set[int] = set(nodes)
    sib_pos = 0
    siblings = proof.siblings
    L = head.length
    while L > 1:
        handled: Set[int] = set()
        new_nodes: Dict[int, bytes] = {}
        for i in sorted(known):
            if i in handled:
                continue
            s = i ^ 1
            handled.add(i)
            handled.add(s)
            if s in nodes:
                sib = nodes[s]
            else:
                if sib_pos >= len(siblings):
                    return False                   # proof too short
                try:
                    sib = bytes.fromhex(siblings[sib_pos])
                except ValueError:
                    return False
                sib_pos += 1
            left, right = (nodes[i], sib) if i < s else (sib, nodes[i])
            new_nodes[i // 2] = _node(left, right)
        nodes = new_nodes
        known = set(nodes)
        L = (L + 1) // 2 if L % 2 == 1 else L // 2  # padded-level shrink (duplicate-last)

    if sib_pos != len(siblings):                   # leftover siblings ⇒ malformed proof
        return False
    if len(nodes) != 1 or 0 not in nodes:
        return False
    return nodes[0].hex() == head.root
