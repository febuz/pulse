"""Erlay-style bounded set reconciliation over CIDs in ~O(diff).

Two peers that have both been gossiping a busy Web hold *almost* the same
inventory: their CID sets overlap by far more than they differ. The cheapest
way to relay a new record is the inventory ``inv -> getdata`` path
(:mod:`knitweb.p2p.inventory`), but that still requires *announcing* every CID
— an ``inv`` flood proportional to the **whole** inventory, not to the handful
of records the peer actually lacks. After a partition heals, or when a fresh
peer joins a long-lived Web, that announce flood is the dominant cost.

Bitcoin's **Erlay** (BIP-330) attacks exactly this: instead of announcing the
full inventory, two peers *reconcile* — they exchange compact summaries and
converge on their **symmetric set difference** in traffic proportional to the
size of the *difference*, not the inventory. Erlay's production core uses
PinSketch / BCH set sketches (Minisketch) to decode the difference from a
single fixed-size sketch; that decoder is heavy linear-algebra over GF(2^k) and
is deliberately **out of scope** here (it is not stdlib, and it needs float-free
field math we do not want to vendor).

This module ports the *tractable, stdlib-friendly* half of the idea — recursive
**range bisection** over the lexically-sorted CID set, the same shape as a
Merkle-trie range diff:

  * A peer summarizes a half-open CID range ``[lo, hi)`` of its sorted inventory
    by an integer ``(count, fingerprint)`` where ``count`` is how many of its
    CIDs fall in the range and ``fingerprint`` is the XOR of their SHA-256
    digests (an order-independent, integer, collision-resistant set fold).
  * Two peers compare summaries for the *same* range. Matching
    ``(count, fingerprint)`` proves the range is **identical** on both sides
    (XOR-fold equality), so it is pruned — no CIDs travel for the overlap.
  * A mismatch **bisects** the range into ``FANOUT`` bounded child sub-ranges
    (split on the CID byte-string keyspace) and recurses, so probing zeroes in
    on the differing CIDs in ``~O(diff)`` small fixed-size summary frames.
  * When a range is small enough (``<= LEAF_MAX`` CIDs on either side) it is a
    **leaf**: the peers exchange the raw CID lists for just that range and take
    the set difference directly. The leaf bound caps how many CIDs a single
    mismatching frame carries.

The resulting *missing-CID set* is handed to the existing inventory
``getdata`` path (:meth:`knitweb.p2p.inventory.InventoryRelay.on_getdata`) for
byte-identical body fetch — reconciliation only ever moves **CIDs**, never
record bodies, so a signed record's byte-identity (and therefore its CID) is
untouched by this module.

Like :mod:`knitweb.p2p.inventory` and :mod:`knitweb.p2p.anti_entropy` it is a
**transport-free, socket-free state machine**: every method consumes/produces
``bytes`` frames built with :func:`knitweb.p2p.wire.write_frame_bytes`, never a
socket, so the O(diff)-convergence property is provable without a real peer and
it cannot stall on a handshake. A node adopts it by feeding inbound reconcile
frames to :meth:`Reconciler.on_frame` and writing back the returned reply
frames; the loop terminates when both sides have pruned every range.

Determinism: the only orderings are **lexical CID order** and **integer XOR**;
recursion depth and children-per-level are integer-bounded; there is no
wall-clock and no randomness on any path. Two peers replaying the same pair of
CID sets always exchange the identical sequence of frames and learn the
identical difference.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from . import wire

__all__ = [
    "ReconcileError",
    "RECONCILE_PROBE",
    "RECONCILE_LEAF",
    "FANOUT",
    "LEAF_MAX",
    "MAX_DEPTH",
    "FULL_LO",
    "FULL_HI",
    "cid_fingerprint",
    "range_summary",
    "split_range",
    "build_probe_frame",
    "parse_probe_frame",
    "build_leaf_frame",
    "parse_leaf_frame",
    "Reconciler",
    "reconcile_pair",
]

# Frame kinds, namespaced under ``reconcile-`` so they never collide with the
# ``inv-*`` inventory kinds or any ``fabric-*`` record kind on the wire.
RECONCILE_PROBE = "reconcile-probe"  # one range's (lo, hi, count, fingerprint)
RECONCILE_LEAF = "reconcile-leaf"    # the raw CID list for one small range

# Children-per-level when a range mismatches. A binary split (2) is the minimal
# Erlay-style bisection; a wider fanout trades more bytes/level for fewer levels.
FANOUT = 4

# A range with at most this many CIDs (on the side summarizing it) is a leaf:
# the peer ships its raw CID list instead of recursing. Bounds the CID count a
# single mismatch frame carries — the whole point of reconciling over flooding.
LEAF_MAX = 8

# Hard recursion-depth ceiling. The keyspace is split byte-string-lexically, so
# depth is bounded by the keyspace anyway; this is the defensive backstop that
# guarantees bounded compute even on adversarial inputs.
MAX_DEPTH = 64

# The full CID keyspace as a half-open ``[FULL_LO, FULL_HI)`` byte interval.
# CIDs are printable strings (base32/hex multihash text); the empty string is a
# lower bound below every CID and ``"￿"`` is an upper bound above every
# printable CID, so the root range covers the entire sorted inventory.
FULL_LO = ""
FULL_HI = "￿"

# Cap on raw CIDs a single leaf frame may carry — a peer cannot force an
# unbounded allocation by claiming a giant leaf. wire.MAX_FRAME_BYTES backstops.
MAX_LEAF_CIDS = 100_000

# The XOR-fold fingerprint is a SHA-256-width value: 32 bytes / 256 bits. It
# travels as a fixed-width byte string (canonical CBOR major type 2) rather than
# an integer because CBOR caps integers at 64 bits; the compute stays integer
# (``int.from_bytes`` / ``int.to_bytes``), only the wire encoding is bytes.
FINGERPRINT_BYTES = 32


class ReconcileError(ValueError):
    """Raised for malformed or unsafe reconcile frames / arguments."""


# ---------------------------------------------------------------------------
# Integer set fold: count + XOR-of-SHA256 fingerprint over a CID range
# ---------------------------------------------------------------------------

def cid_fingerprint(cids: Iterable[str]) -> int:
    """Return the order-independent integer fingerprint of a CID set.

    The fingerprint is the XOR-fold of ``SHA-256(cid_utf8)`` over the set,
    read as a big-endian 256-bit integer. XOR is associative and commutative,
    so the fold is **independent of iteration order** — two peers holding the
    same set of CIDs in a range compute the identical fingerprint regardless of
    how they sorted or stored them. A duplicate CID would XOR-cancel itself, so
    callers must pass a de-duplicated set (the :class:`Reconciler` does).

    This is the integer, float-free, collision-resistant analogue of Erlay's
    set sketch: a fixed-width summary whose equality proves set equality with
    cryptographic confidence, while travelling in a tiny frame regardless of how
    many CIDs the range holds.
    """
    acc = 0
    for cid in cids:
        if not isinstance(cid, str):
            raise ReconcileError("cid must be str")
        if not cid:
            raise ReconcileError("cid must be non-empty")
        digest = hashlib.sha256(cid.encode("utf-8")).digest()
        acc ^= int.from_bytes(digest, "big")
    return acc


def range_summary(sorted_cids: Sequence[str], lo: str, hi: str) -> Tuple[int, int]:
    """Summarize the half-open range ``[lo, hi)`` of a sorted CID list.

    Returns ``(count, fingerprint)``: how many CIDs fall in ``lo <= cid < hi``
    and the XOR-of-SHA256 fingerprint of exactly those CIDs. ``sorted_cids``
    must be lexically sorted and de-duplicated. The summary is what travels in a
    probe frame; equal ``(count, fingerprint)`` on both peers proves the range
    is identical and can be pruned.
    """
    in_range = _slice_range(sorted_cids, lo, hi)
    return len(in_range), cid_fingerprint(in_range)


def _slice_range(sorted_cids: Sequence[str], lo: str, hi: str) -> List[str]:
    """Return the sub-list of ``sorted_cids`` with ``lo <= cid < hi``.

    Uses bisection on the already-sorted list, so a range slice is O(log n + k)
    rather than a full scan — keeping per-probe work bounded on a large
    inventory.
    """
    import bisect

    left = bisect.bisect_left(sorted_cids, lo)
    right = bisect.bisect_left(sorted_cids, hi)
    return list(sorted_cids[left:right])


def split_range(lo: str, hi: str, fanout: int = FANOUT) -> List[Tuple[str, str]]:
    """Split ``[lo, hi)`` into ``fanout`` bounded lexical child sub-ranges.

    The split is over the **byte-string keyspace**, not the population, so it is
    a pure function of the bounds alone — both peers derive the identical child
    boundaries without exchanging them, and an empty child simply summarizes to
    ``(0, 0)``. Boundaries are computed by mapping the shared prefix-stripped
    first code points to integers and partitioning that integer interval into
    ``fanout`` equal pieces; a non-splittable range (the bounds are adjacent or
    equal) returns a single child equal to itself, which the depth ceiling then
    forces into a leaf.
    """
    if fanout < 2:
        raise ReconcileError("fanout must be >= 2")
    lo_key = _key_int(lo)
    hi_key = _key_int(hi)
    if hi_key <= lo_key + 1:
        # Keyspace exhausted at this granularity: cannot bisect further.
        return [(lo, hi)]
    width = hi_key - lo_key
    children: List[Tuple[str, str]] = []
    for i in range(fanout):
        a = lo_key + (width * i) // fanout
        b = lo_key + (width * (i + 1)) // fanout
        if i == fanout - 1:
            b = hi_key
        if b <= a:
            continue
        child_lo = lo if i == 0 else _int_key(a)
        child_hi = hi if i == fanout - 1 else _int_key(b)
        if child_hi <= child_lo:
            continue
        children.append((child_lo, child_hi))
    if not children:
        return [(lo, hi)]
    return children


# CIDs are short printable strings; we partition the keyspace by reading a fixed
# integer "key" from the leading code points of a bound. Three leading code
# points (each < 2**21 for valid Unicode, but bounded to 2**16 here since CIDs
# are ASCII/base32 text) give ample fanout resolution while staying integer-only.
_KEY_RADIX = 1 << 16
_KEY_CHARS = 6


def _key_int(s: str) -> int:
    """Map a bound string to an integer key over its leading code points.

    Big-endian, base ``_KEY_RADIX``, over the first ``_KEY_CHARS`` code points
    (zero-padded). Monotonic in lexical order for the printable CID alphabet, so
    ``a < b`` lexically implies ``_key_int(a) <= _key_int(b)``; combined with the
    bisect slice this makes the keyspace split deterministic and integer-only.
    """
    if s == FULL_HI:
        # Sentinel upper bound: strictly above every printable key.
        return _KEY_RADIX ** _KEY_CHARS
    acc = 0
    for i in range(_KEY_CHARS):
        acc *= _KEY_RADIX
        if i < len(s):
            cp = ord(s[i])
            acc += cp if cp < _KEY_RADIX else _KEY_RADIX - 1
    return acc


def _int_key(value: int) -> str:
    """Inverse of :func:`_key_int` for a midpoint integer -> bound string.

    Produces the lexically-smallest string whose ``_key_int`` is ``value``,
    suitable as a half-open boundary. Trailing zero code points are dropped so
    the boundary stays a clean prefix; this never collides two distinct
    midpoints because the radix digits are recovered exactly.
    """
    if value >= _KEY_RADIX ** _KEY_CHARS:
        return FULL_HI
    digits: List[int] = []
    for _ in range(_KEY_CHARS):
        digits.append(value % _KEY_RADIX)
        value //= _KEY_RADIX
    digits.reverse()
    # Drop trailing zero code points so the boundary is a minimal prefix.
    while digits and digits[-1] == 0:
        digits.pop()
    return "".join(chr(d) for d in digits)


# ---------------------------------------------------------------------------
# Frame codec
# ---------------------------------------------------------------------------

def _check_bounds(lo, hi) -> Tuple[str, str]:
    if not isinstance(lo, str) or not isinstance(hi, str):
        raise ReconcileError("range bounds must be str")
    if not (lo < hi):
        raise ReconcileError("range bounds must satisfy lo < hi")
    return lo, hi


def build_probe_frame(lo: str, hi: str, count: int, fingerprint: int, depth: int) -> bytes:
    """Build one length-prefixed ``reconcile-probe`` frame.

    Carries the range bounds, the integer ``(count, fingerprint)`` summary, and
    the integer recursion ``depth`` (so the responder enforces the same depth
    ceiling). Fixed, tiny size regardless of how many CIDs the range holds.
    """
    _check_bounds(lo, hi)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ReconcileError("count must be a non-negative int")
    if not isinstance(fingerprint, int) or isinstance(fingerprint, bool) or fingerprint < 0:
        raise ReconcileError("fingerprint must be a non-negative int")
    if fingerprint >= (1 << (8 * FINGERPRINT_BYTES)):
        raise ReconcileError("fingerprint exceeds 256 bits")
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
        raise ReconcileError("depth must be a non-negative int")
    return wire.write_frame_bytes(
        {
            "kind": RECONCILE_PROBE,
            "lo": lo,
            "hi": hi,
            "count": count,
            "fp": fingerprint.to_bytes(FINGERPRINT_BYTES, "big"),
            "depth": depth,
        }
    )


def parse_probe_frame(frame: bytes) -> Tuple[str, str, int, int, int]:
    """Parse a ``reconcile-probe`` frame -> ``(lo, hi, count, fingerprint, depth)``."""
    msg = wire.read_frame_bytes(frame)
    if msg.get("kind") != RECONCILE_PROBE:
        raise ReconcileError("not a reconcile-probe frame")
    lo, hi = _check_bounds(msg.get("lo"), msg.get("hi"))
    count = msg.get("count")
    fp_bytes = msg.get("fp")
    depth = msg.get("depth")
    for name, val in (("count", count), ("depth", depth)):
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise ReconcileError(f"{name} must be a non-negative int")
    if not isinstance(fp_bytes, bytes) or len(fp_bytes) != FINGERPRINT_BYTES:
        raise ReconcileError(f"fp must be {FINGERPRINT_BYTES} bytes")
    fp = int.from_bytes(fp_bytes, "big")
    return lo, hi, count, fp, depth


def _check_leaf_cids(cids: Iterable[str]) -> List[str]:
    out: List[str] = []
    for cid in cids:
        if not isinstance(cid, str):
            raise ReconcileError("cid must be str")
        if not cid:
            raise ReconcileError("cid must be non-empty")
        out.append(cid)
    if len(out) > MAX_LEAF_CIDS:
        raise ReconcileError(f"too many cids in one leaf: {len(out)} > {MAX_LEAF_CIDS}")
    return out


def build_leaf_frame(lo: str, hi: str, cids: Iterable[str]) -> bytes:
    """Build one length-prefixed ``reconcile-leaf`` frame.

    Carries the raw, lexically-sorted CID list for a small leaf range so the
    other peer can take the set difference directly. Only CIDs travel — never a
    record body — so byte-identity of any signed record is untouched.
    """
    _check_bounds(lo, hi)
    return wire.write_frame_bytes(
        {"kind": RECONCILE_LEAF, "lo": lo, "hi": hi, "cids": _check_leaf_cids(cids)}
    )


def parse_leaf_frame(frame: bytes) -> Tuple[str, str, List[str]]:
    """Parse a ``reconcile-leaf`` frame -> ``(lo, hi, cids)``."""
    msg = wire.read_frame_bytes(frame)
    if msg.get("kind") != RECONCILE_LEAF:
        raise ReconcileError("not a reconcile-leaf frame")
    lo, hi = _check_bounds(msg.get("lo"), msg.get("hi"))
    cids = msg.get("cids")
    if not isinstance(cids, list):
        raise ReconcileError("leaf cids must be a list")
    return lo, hi, _check_leaf_cids(cids)


# ---------------------------------------------------------------------------
# Reconciler — the range-bisection state machine
# ---------------------------------------------------------------------------

@dataclass
class _RangeState:
    """Bookkeeping for one in-flight range probe (no clock, no randomness)."""

    lo: str
    hi: str
    depth: int


class Reconciler:
    """Drives recursive range-bisection reconciliation for one node.

    The reconciler holds the node's **own** sorted, de-duplicated CID set and a
    record of which range probes are in flight. It is symmetric: both peers run
    one. The protocol is initiator-driven for clarity — the *initiator* opens by
    probing the full keyspace; the *responder* answers each probe by either
    pruning (summaries match), bisecting (mismatch, range still large), or
    turning the range into a leaf exchange (mismatch, range small). Missing CIDs
    the responder learns about are surfaced via :attr:`missing` for handoff to
    the inventory ``getdata`` path.

    Socket-free: :meth:`open` returns the initial probe frame(s); every inbound
    frame goes to :meth:`on_frame`, which returns the reply frame(s). The loop
    ends when neither side produces a reply. Determinism is total: replaying the
    same two CID sets always yields the identical frame sequence and the
    identical learned difference.
    """

    def __init__(
        self,
        cids: Iterable[str],
        *,
        fanout: int = FANOUT,
        leaf_max: int = LEAF_MAX,
        max_depth: int = MAX_DEPTH,
    ) -> None:
        if fanout < 2:
            raise ReconcileError("fanout must be >= 2")
        if leaf_max < 1:
            raise ReconcileError("leaf_max must be >= 1")
        if max_depth < 1:
            raise ReconcileError("max_depth must be >= 1")
        # De-duplicate and lexically sort once; all range work bisects this.
        deduped = set()
        for c in cids:
            if not isinstance(c, str):
                raise ReconcileError("cid must be str")
            if not c:
                raise ReconcileError("cid must be non-empty")
            deduped.add(c)
        self._sorted: List[str] = sorted(deduped)
        self._have: set = deduped
        self._fanout = fanout
        self._leaf_max = leaf_max
        self._max_depth = max_depth
        # CIDs this node lacks but the peer holds — the reconciliation output.
        self.missing: set = set()
        # Ranges for which we have already emitted a leaf reply. A leaf exchange
        # is exactly one round-trip: a peer that has already answered a leaf for
        # a range must not answer again, or two peers each holding an extra in
        # the same leaf would ping-pong forever. Keyed on (lo, hi) byte bounds.
        self._answered_leaves: set = set()

    # -- summaries --------------------------------------------------------

    def _summary(self, lo: str, hi: str) -> Tuple[int, int]:
        return range_summary(self._sorted, lo, hi)

    def _range_cids(self, lo: str, hi: str) -> List[str]:
        return _slice_range(self._sorted, lo, hi)

    # -- initiator open ---------------------------------------------------

    def open(self) -> List[bytes]:
        """Open reconciliation by probing the full CID keyspace.

        Returns a single ``reconcile-probe`` frame summarizing this node's whole
        inventory. The peer answers via :meth:`on_frame`.
        """
        count, fp = self._summary(FULL_LO, FULL_HI)
        return [build_probe_frame(FULL_LO, FULL_HI, count, fp, 0)]

    # -- inbound frame dispatch ------------------------------------------

    def on_frame(self, frame: bytes) -> List[bytes]:
        """Handle one inbound reconcile frame; return reply frame(s).

        Dispatches on frame kind. A probe whose summary matches ours prunes the
        range (no reply). A mismatch bisects (probe replies) or, once small
        enough or at the depth ceiling, turns into a leaf exchange. A leaf frame
        lets us learn exactly which CIDs we lack and reply with the CIDs the peer
        lacks. Returns ``[]`` when the range is fully reconciled — the loop's
        natural termination.
        """
        kind = wire.read_frame_bytes(frame).get("kind")
        if kind == RECONCILE_PROBE:
            return self._on_probe(frame)
        if kind == RECONCILE_LEAF:
            return self._on_leaf(frame)
        raise ReconcileError(f"unknown reconcile frame kind: {kind!r}")

    def _on_probe(self, frame: bytes) -> List[bytes]:
        lo, hi, their_count, their_fp, depth = parse_probe_frame(frame)
        my_count, my_fp = self._summary(lo, hi)
        if my_count == their_count and my_fp == their_fp:
            # Identical range on both sides: prune, nothing to exchange.
            return []
        my_cids = self._range_cids(lo, hi)
        # Leaf when either side's population is small, or we hit the depth/keyspace
        # ceiling — bounds the CID count any single frame carries.
        children = split_range(lo, hi, self._fanout)
        unsplittable = len(children) == 1 and children[0] == (lo, hi)
        if (
            len(my_cids) <= self._leaf_max
            or their_count <= self._leaf_max
            or depth >= self._max_depth
            or unsplittable
        ):
            # Send our raw CIDs for this range; the peer diffs and replies with
            # what we lack as its own leaf.
            return [build_leaf_frame(lo, hi, my_cids)]
        # Mismatch with room to bisect: probe each child sub-range.
        out: List[bytes] = []
        for clo, chi in children:
            ccount, cfp = self._summary(clo, chi)
            out.append(build_probe_frame(clo, chi, ccount, cfp, depth + 1))
        return out

    def _on_leaf(self, frame: bytes) -> List[bytes]:
        lo, hi, their_cids = parse_leaf_frame(frame)
        their_set = set(their_cids)
        # CIDs the peer has in this range that we lack -> we must getdata them.
        for cid in their_cids:
            if cid not in self._have:
                self.missing.add(cid)
        # CIDs we have in this range that the peer lacks -> reply so the peer can
        # learn its own missing set. The reply is sent at most once per range: a
        # leaf exchange is a single round-trip, so once we have answered a leaf
        # for this range we stay silent (otherwise two peers each holding an
        # extra in the same leaf would ping-pong forever). We also stay silent
        # when we have no extras the peer lacks — that already terminates.
        key = (lo, hi)
        if key in self._answered_leaves:
            return []
        my_cids = self._range_cids(lo, hi)
        peer_lacks = [c for c in my_cids if c not in their_set]
        if not peer_lacks:
            return []
        self._answered_leaves.add(key)
        return [build_leaf_frame(lo, hi, my_cids)]


# ---------------------------------------------------------------------------
# Test/adoption helper: drive a full reconciliation between two CID sets.
# ---------------------------------------------------------------------------

def reconcile_pair(
    a_cids: Iterable[str],
    b_cids: Iterable[str],
    *,
    fanout: int = FANOUT,
    leaf_max: int = LEAF_MAX,
    max_depth: int = MAX_DEPTH,
) -> Dict[str, object]:
    """Drive a complete socket-free reconciliation between two CID sets.

    ``a`` is the initiator. Returns a dict with:

      * ``a_missing`` / ``b_missing`` — the CID sets each side learned it lacks
        (and would now ``getdata``);
      * ``rounds`` — the integer number of message rounds (frame batches) the
        exchange took, the ~O(diff) cost metric;
      * ``frames`` — total integer frame count exchanged.

    This is the reusable driver a node (or a test) uses to confirm two peers
    converge on their exact symmetric difference. It is pure logic: no socket,
    no clock, no randomness, so it cannot stall and always terminates inside the
    bounded recursion depth.
    """
    a = Reconciler(a_cids, fanout=fanout, leaf_max=leaf_max, max_depth=max_depth)
    b = Reconciler(b_cids, fanout=fanout, leaf_max=leaf_max, max_depth=max_depth)

    # The initiator opens; frames ping-pong until both sides fall silent. We
    # alternate which reconciler receives the current batch.
    pending: List[bytes] = a.open()
    receiver, sender = b, a
    rounds = 0
    frames = 0
    # Bound on total rounds: the keyspace partition depth times a safety factor.
    # Guarantees termination even on a pathological input (defensive).
    max_rounds = (max_depth + 2) * (fanout + 2) * 64
    while pending:
        rounds += 1
        frames += len(pending)
        if rounds > max_rounds:
            raise ReconcileError("reconciliation did not converge within bound")
        replies: List[bytes] = []
        for frame in pending:
            replies.extend(receiver.on_frame(frame))
        pending = replies
        receiver, sender = sender, receiver

    return {
        "a_missing": a.missing,
        "b_missing": b.missing,
        "rounds": rounds,
        "frames": frames,
    }
