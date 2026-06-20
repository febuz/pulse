"""Sorted Merkle status tree — membership AND non-membership proofs for revocation.

Revocation needs the opposite of what the fabric already has. ``fabric.feed_proof`` gives
*inclusion* proofs over an append-ordered, **untagged** Merkle tree (``crypto.merkle_root``);
a voter must instead prove **non-revocation** — that their (random) ``revocation_pointer``
is *absent* from the revoked set — without downloading the whole set and without the tree
leaking who was revoked.

This module builds the missing primitive with stdlib + ``crypto.sha256`` only:

  * **Sorted leaves.** Revoked pointers are sorted bytewise, so index adjacency equals value
    adjacency. A non-membership proof for ``q`` is then the inclusion proof(s) of the two
    *adjacent* committed leaves that bracket it (``lo < q < hi``), or the single boundary leaf
    when ``q`` is below the first / above the last, or "empty tree" when nothing is revoked.
  * **Domain-tagged hashing.** ``leaf = sha256(0x00 ‖ pointer)`` and
    ``node = sha256(0x01 ‖ left ‖ right)`` separate leaf and interior hashes, closing the
    CVE-2012-2459 leaf/interior-confusion and second-preimage gaps that the untagged
    ``crypto.merkle_root`` has (and which make it unsound for status proofs). The
    duplicate-last-on-odd rule mirrors ``feed_proof`` so the sibling-path code is identical
    in shape, only the hash is tagged.

Soundness rests on the root being **signed by the revocation authority over a sorted, complete
set** (done in :mod:`knitweb.personhood.revocation` via a signed feed head that commits both
``root`` and ``length``). Given that signed (root, length), a revoked voter cannot fabricate
adjacent leaves that strictly bracket their own pointer, and a verifier reconstructs every
proof against the signed root (a forged sibling path fails second-preimage).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from ..core import crypto

__all__ = [
    "EMPTY_ROOT",
    "MembershipProof",
    "NonMembershipProof",
    "StatusTree",
    "verify_membership",
    "verify_non_membership",
]

_LEAF_TAG = b"\x00"
_NODE_TAG = b"\x01"
_POINTER_BYTES = 32

# A well-defined, domain-separated root for the empty (nothing-revoked) tree.
EMPTY_ROOT = crypto.sha256(b"knitweb-personhood-status:empty:v1").hex()


def _leaf(pointer: bytes) -> bytes:
    return crypto.sha256(_LEAF_TAG + pointer)


def _node(left: bytes, right: bytes) -> bytes:
    return crypto.sha256(_NODE_TAG + left + right)


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


def _path(leaves: List[bytes], index: int) -> List[Tuple[str, bool]]:
    levels = _levels(leaves)
    path: List[Tuple[str, bool]] = []
    idx = index
    for level in levels[:-1]:
        sib = idx ^ 1
        path.append((level[sib].hex(), sib > idx))
        idx //= 2
    return path


def _reconstruct(leaf: bytes, path: List[Tuple[str, bool]]) -> bytes:
    h = leaf
    for sib_hex, sib_is_right in path:
        sib = bytes.fromhex(sib_hex)
        h = _node(h, sib) if sib_is_right else _node(sib, h)
    return h


def _as_pointer(pointer_hex: str) -> bytes:
    if not crypto.is_valid_hex(pointer_hex, _POINTER_BYTES):
        raise ValueError("revocation pointer must be 32-byte hex")
    return bytes.fromhex(pointer_hex)


@dataclass(frozen=True)
class MembershipProof:
    """A sibling path proving ``pointer`` sits at ``index`` in a tree of ``length`` leaves."""

    index: int
    length: int
    pointer: str  # 32-byte hex of the committed (revoked) leaf
    path: List[Tuple[str, bool]]


@dataclass(frozen=True)
class NonMembershipProof:
    """Bracketing proof that ``pointer`` is absent.

    ``lo`` = inclusion of the greatest revoked pointer < ``pointer`` (or None if below all /
    empty); ``hi`` = inclusion of the smallest revoked pointer > ``pointer`` (or None if above
    all / empty).
    """

    pointer: str
    lo: Optional[MembershipProof]
    hi: Optional[MembershipProof]


class StatusTree:
    """A sorted Merkle tree over revoked ``revocation_pointer`` hexes."""

    def __init__(self, revoked: Iterable[str]) -> None:
        unique = {_as_pointer(p) for p in revoked}
        self._sorted: List[bytes] = sorted(unique)
        self._hex: List[str] = [p.hex() for p in self._sorted]

    @property
    def length(self) -> int:
        return len(self._sorted)

    @property
    def revoked(self) -> List[str]:
        return list(self._hex)

    def root(self) -> str:
        if not self._sorted:
            return EMPTY_ROOT
        return _levels([_leaf(p) for p in self._sorted])[-1][0].hex()

    def contains(self, pointer_hex: str) -> bool:
        p = _as_pointer(pointer_hex)
        i = bisect.bisect_left(self._sorted, p)
        return i < len(self._sorted) and self._sorted[i] == p

    def _membership_at(self, index: int) -> MembershipProof:
        leaves = [_leaf(p) for p in self._sorted]
        return MembershipProof(
            index=index,
            length=len(self._sorted),
            pointer=self._hex[index],
            path=_path(leaves, index),
        )

    def prove_membership(self, pointer_hex: str) -> MembershipProof:
        p = _as_pointer(pointer_hex)
        i = bisect.bisect_left(self._sorted, p)
        if i >= len(self._sorted) or self._sorted[i] != p:
            raise KeyError(f"{pointer_hex} is not revoked (no membership proof)")
        return self._membership_at(i)

    def prove_non_membership(self, pointer_hex: str) -> NonMembershipProof:
        p = _as_pointer(pointer_hex)
        i = bisect.bisect_left(self._sorted, p)
        if i < len(self._sorted) and self._sorted[i] == p:
            raise KeyError(f"{pointer_hex} IS revoked (no non-membership proof)")
        lo = self._membership_at(i - 1) if i > 0 else None
        hi = self._membership_at(i) if i < len(self._sorted) else None
        return NonMembershipProof(pointer=p.hex(), lo=lo, hi=hi)


# ---------------------------------------------------------------------------
# Verification — against an authority-committed (signed) (root, length)
# ---------------------------------------------------------------------------

def _expected_height(length: int) -> int:
    """Path length (tree height) for a duplicate-last Merkle tree of ``length`` leaves."""
    height = 0
    n = length
    while n > 1:
        n += n % 2  # duplicate the last node on an odd level
        n //= 2
        height += 1
    return height


def _index_from_path(path: List[Tuple[str, bool]]) -> int:
    """Recover the leaf position the sibling path commits to, from its direction bits.

    At level ``i`` the prover is the *right* child (bit set) exactly when its sibling sits
    on the *left* (``sib_is_right`` is False). This makes the index a function of the path,
    so it cannot be relabelled independently of the proof.
    """
    recovered = 0
    for i, (_sibling_hex, sib_is_right) in enumerate(path):
        if not sib_is_right:
            recovered |= (1 << i)
    return recovered


def verify_membership(root_hex: str, length: int, proof: MembershipProof) -> bool:
    """True iff ``proof`` shows its pointer is committed at its index in (root, length).

    Critically, the claimed ``index`` is **bound to the path**: the path length must equal
    the committed tree height and the index recovered from the path's direction bits must
    equal ``proof.index``. Without this binding the index is decorative, and a non-membership
    adjacency proof could be forged by relabelling the indices of genuine bracketing proofs.
    """
    if proof.length != length:
        return False
    if not 0 <= proof.index < length:
        return False
    if len(proof.path) != _expected_height(length):
        return False
    if _index_from_path(proof.path) != proof.index:
        return False
    try:
        leaf = _leaf(_as_pointer(proof.pointer))
    except ValueError:
        return False
    return _reconstruct(leaf, proof.path).hex() == root_hex


def verify_non_membership(root_hex: str, length: int, proof: NonMembershipProof) -> bool:
    """True iff ``proof`` shows its pointer is absent from the committed (root, length)."""
    try:
        q = _as_pointer(proof.pointer)
    except ValueError:
        return False

    if length == 0:
        return proof.lo is None and proof.hi is None and root_hex == EMPTY_ROOT

    if proof.lo is None:
        # q is below the first revoked pointer.
        if proof.hi is None or proof.hi.index != 0:
            return False
        return verify_membership(root_hex, length, proof.hi) and q < _as_pointer(proof.hi.pointer)

    if proof.hi is None:
        # q is above the last revoked pointer.
        if proof.lo.index != length - 1:
            return False
        return verify_membership(root_hex, length, proof.lo) and _as_pointer(proof.lo.pointer) < q

    # q sits strictly between two adjacent committed leaves.
    if proof.hi.index != proof.lo.index + 1:
        return False
    if not (verify_membership(root_hex, length, proof.lo) and verify_membership(root_hex, length, proof.hi)):
        return False
    return _as_pointer(proof.lo.pointer) < q < _as_pointer(proof.hi.pointer)
