"""Inventory relay — announce/want CID dedup to replace full-flood gossip.

``FabricNode.weave`` currently pushes every woven record to every peer
(see :meth:`knitweb.fabric.node.FabricNode._broadcast`). That is an O(N*size)
flood: a popular Web re-sends the *whole* record body to a peer that may already
hold it, amplifying traffic and blowing the doc's latency budget for a hot Web.

Real P2P stacks long ago abandoned blind flooding for a two-step *lazy
announce*:

  * **Bitcoin Core** relays an ``inv`` (inventory) of transaction/block *hashes*
    first; a peer replies ``getdata`` only for the hashes it lacks, and the full
    body travels exactly once per peer that needs it. A ``SeenSet`` (Core's
    ``m_recently_announced`` / ``filterInventoryKnown``) suppresses re-announcing
    an item a peer already knows.
  * **libp2p gossipsub** does the same with ``IHAVE`` (here is a message-id I
    have) / ``IWANT`` (send me that id) on its lazy-push mesh edges.

This module ports that pattern faithfully but idiomatically to the knitweb wire:

  * A node **announces** the canonical CID of a record (``inv`` frame). The CID
    is exactly ``core.canonical.cid(record)`` — the same identity the Web indexes
    by — so no body, signature, or re-encoding travels in an announcement.
  * A receiving peer diffs the announced CIDs against what it already holds and
    **wants** (``getdata`` frame) only the CIDs it lacks.
  * The announcer answers a want by returning the **stored frame bytes verbatim**
    (``record`` frame): no decode/re-encode round-trip, so a signed record's
    byte-identity — and therefore its CID — is preserved exactly.
  * A bounded-integer-LRU :class:`SeenSet` keyed on CID dedups both directions:
    a CID already seen is neither re-announced nor re-wanted, cutting redundant
    traffic from O(N) floods to O(diff).

It is a **transport-free, socket-free core** like :mod:`knitweb.p2p.anti_entropy`:
the state machine consumes/produces wire *frames* (built from
:func:`knitweb.p2p.wire.write_frame_bytes` / parsed with
:func:`~knitweb.p2p.wire.read_frame_bytes`) and never touches a socket, so its
dedup/diff behaviour is provable without a real peer and it can be driven through
the anti-entropy :data:`~knitweb.p2p.anti_entropy.SyncRound` callback pattern
*without editing* ``fabric/node.py``. It touches no reputation gate; CRDT
idempotency in the Web already guarantees convergence regardless of arrival
order, so re-delivering an already-held CID is harmless — this module just makes
it rare.

Determinism: the only ordering is insertion order (an ``OrderedDict`` LRU) and
the integer capacity bound; there is no wall-clock and no randomness on any path,
so two peers replaying the same announce/want sequence evolve identical state.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Tuple

from ..core import canonical
from . import wire

__all__ = [
    "InventoryError",
    "SeenSet",
    "INV",
    "GETDATA",
    "RECON_REQ",
    "RECON_RANGE",
    "RECON_RESULT",
    "MAX_RECON_FRAMES",
    "record_cid",
    "build_inv_frame",
    "parse_inv_frame",
    "build_getdata_frame",
    "parse_getdata_frame",
    "build_recon_frame",
    "parse_recon_frame",
    "InventoryRelay",
]

# Frame kinds. These are namespaced under ``inv-`` so they never collide with the
# existing ``fabric-record`` / ``equivocation-report`` record kinds on the wire.
INV = "inv-announce"
GETDATA = "inv-getdata"

# Erlay activation (#60): the inv-reconcile message family. A reconcile *session*
# (the recursive range/bucket bisection in :mod:`knitweb.p2p.reconcile`) is a
# multi-round ping-pong of ``reconcile-probe`` / ``reconcile-leaf`` frames. To
# ride the one-request/one-response dict carrier (a pure-push topology where the
# peer needs no route back to the initiator), each round's *batch* of reconcile
# frames travels inside ONE of these envelopes:
#
#   * ``inv-recon-req``    — the initiator opens a session: its first probe batch
#                            (a single full-keyspace ``reconcile-probe``).
#   * ``inv-recon-range``  — a subsequent batch of probe/leaf frames the initiator
#                            sends after receiving the responder's last batch.
#   * ``inv-recon-result`` — the responder's reply batch (what its Reconciler
#                            produced for the batch it just received); an empty
#                            batch signals the responder pruned/answered all ranges
#                            and the session has converged.
#
# Only ``(count, integer-xor-fingerprint)`` range summaries and bare CID *lists*
# ever ride these envelopes — never a record body — so a signed record's
# byte-identity (and CID) is untouched by reconciliation, exactly as the lazy
# inv -> getdata path keeps it untouched. The differing CIDs the session
# discovers are fetched through the EXISTING inv-getdata path, verbatim.
RECON_REQ = "inv-recon-req"
RECON_RANGE = "inv-recon-range"
RECON_RESULT = "inv-recon-result"

# Cap the number of CIDs a single frame may carry. An inv/getdata is a list of
# fixed-width content addresses; bounding it keeps a frame small (the whole point
# of announcing instead of flooding) and stops a peer from forcing an unbounded
# allocation with one giant frame. wire.MAX_FRAME_BYTES is the hard backstop.
MAX_CIDS_PER_FRAME = 50_000

# Cap the number of reconcile frames a single envelope batch may carry. A bisection
# level fans out by at most ``reconcile.FANOUT`` per mismatching range, so an honest
# batch is small; this bounds a malicious peer from forcing an unbounded batch in
# one envelope. wire.MAX_FRAME_BYTES is the hard backstop.
MAX_RECON_FRAMES = 50_000


class InventoryError(ValueError):
    """Raised for malformed or unsafe inventory frames / arguments."""


# ---------------------------------------------------------------------------
# SeenSet — bounded integer-LRU dedup keyed on CID
# ---------------------------------------------------------------------------

class SeenSet:
    """A bounded, insertion-ordered LRU set of CIDs for message-ID dedup.

    This is the knitweb analogue of Bitcoin Core's ``filterInventoryKnown`` and
    gossipsub's seen-message cache: a CID that has already passed through is not
    re-announced or re-requested, so a record converges to a peer at most once
    even under repeated gossip.

    The set is **bounded** by an integer ``capacity``: once full, adding a new
    CID evicts the least-recently-used one (the oldest by last touch). Memory is
    therefore O(capacity), independent of how many records the Web has ever
    carried — an unbounded seen-set would be a slow memory-exhaustion vector on a
    long-lived node. Touch order is the only state; there is no clock and no
    randomness, so eviction is fully deterministic.
    """

    def __init__(self, capacity: int = 100_000) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool):
            raise TypeError("capacity must be int")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        # value is unused; OrderedDict gives us O(1) membership + LRU ordering.
        self._items: "OrderedDict[str, None]" = OrderedDict()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, cid: str) -> bool:
        return cid in self._items

    def __iter__(self):
        # Oldest -> newest, matching LRU eviction order.
        return iter(self._items)

    def _check_cid(self, cid: str) -> str:
        if not isinstance(cid, str):
            raise TypeError("cid must be str")
        if not cid:
            raise InventoryError("cid must be non-empty")
        return cid

    def add(self, cid: str) -> bool:
        """Mark ``cid`` seen. Return ``True`` iff it was *newly* added.

        A repeat ``add`` of a known CID returns ``False`` and refreshes its LRU
        recency (it is the most-recently-used again) without growing the set.
        """
        cid = self._check_cid(cid)
        if cid in self._items:
            self._items.move_to_end(cid)
            return False
        self._items[cid] = None
        if len(self._items) > self._capacity:
            # Evict the least-recently-used (front of the OrderedDict).
            self._items.popitem(last=False)
        return True

    def add_many(self, cids: Iterable[str]) -> List[str]:
        """Add several CIDs; return the subset that was newly added, in order."""
        return [cid for cid in cids if self.add(cid)]

    def filter_unseen(self, cids: Iterable[str]) -> List[str]:
        """Return the CIDs not currently in the set (read-only; no insertion).

        De-duplicates within the input too, preserving first-seen order, so an
        ``inv`` carrying the same CID twice never produces two wants.
        """
        out: List[str] = []
        local_seen: set = set()
        for cid in cids:
            cid = self._check_cid(cid)
            if cid in self._items or cid in local_seen:
                continue
            local_seen.add(cid)
            out.append(cid)
        return out


# ---------------------------------------------------------------------------
# CID identity + frame codec
# ---------------------------------------------------------------------------

def record_cid(record: Mapping) -> str:
    """Return the canonical CID of a fabric ``record``.

    This is *exactly* ``core.canonical.cid(record)`` — the same content address
    the Web stores a woven record under (:meth:`knitweb.fabric.web.Web.weave`
    calls ``canonical.cid(record)``). Announcing this CID lets a peer decide
    whether it already holds the record without the body ever travelling.
    """
    if not isinstance(record, dict):
        raise InventoryError("record must be a map")
    return canonical.cid(record)


def _check_cid_list(cids: Iterable[str]) -> List[str]:
    out: List[str] = []
    for cid in cids:
        if not isinstance(cid, str):
            raise InventoryError("cid must be str")
        if not cid:
            raise InventoryError("cid must be non-empty")
        out.append(cid)
    if len(out) > MAX_CIDS_PER_FRAME:
        raise InventoryError(
            f"too many cids in one frame: {len(out)} > {MAX_CIDS_PER_FRAME}"
        )
    return out


def build_inv_frame(cids: Iterable[str]) -> bytes:
    """Build one length-prefixed ``inv`` (announce) frame from CIDs.

    Uses :func:`knitweb.p2p.wire.write_frame_bytes` so an inventory frame shares
    the exact framing of every other knitweb message — a 4-byte length prefix in
    front of float-free canonical CBOR.
    """
    return wire.write_frame_bytes({"kind": INV, "cids": _check_cid_list(cids)})


def parse_inv_frame(frame: bytes) -> List[str]:
    """Parse an ``inv`` frame, returning the announced CIDs."""
    msg = wire.read_frame_bytes(frame)
    if msg.get("kind") != INV:
        raise InventoryError("not an inv-announce frame")
    cids = msg.get("cids")
    if not isinstance(cids, list):
        raise InventoryError("inv cids must be a list")
    return _check_cid_list(cids)


def build_getdata_frame(cids: Iterable[str]) -> bytes:
    """Build one length-prefixed ``getdata`` (want) frame from CIDs."""
    return wire.write_frame_bytes({"kind": GETDATA, "cids": _check_cid_list(cids)})


def parse_getdata_frame(frame: bytes) -> List[str]:
    """Parse a ``getdata`` frame, returning the wanted CIDs."""
    msg = wire.read_frame_bytes(frame)
    if msg.get("kind") != GETDATA:
        raise InventoryError("not an inv-getdata frame")
    cids = msg.get("cids")
    if not isinstance(cids, list):
        raise InventoryError("getdata cids must be a list")
    return _check_cid_list(cids)


# ---------------------------------------------------------------------------
# Reconcile envelope codec — carry a batch of reconcile frames over the carrier
# ---------------------------------------------------------------------------

# A reconcile batch is a list of opaque ``reconcile-probe`` / ``reconcile-leaf``
# wire frames (each already a length-prefixed canonical-CBOR frame produced by
# :mod:`knitweb.p2p.reconcile`). The envelope just carries that ordered list of
# byte strings under one ``inv-recon-*`` kind so a round of bisection rides a
# single carrier dial; the envelope never decodes the inner frames, so the
# byte-identity of each reconcile frame (and the determinism of the session) is
# preserved exactly.


def _check_recon_batch(frames: Iterable[bytes]) -> List[bytes]:
    out: List[bytes] = []
    for fr in frames:
        if not isinstance(fr, (bytes, bytearray)):
            raise InventoryError("reconcile batch entries must be bytes")
        out.append(bytes(fr))
    if len(out) > MAX_RECON_FRAMES:
        raise InventoryError(
            f"too many reconcile frames in one batch: {len(out)} > {MAX_RECON_FRAMES}"
        )
    return out


def build_recon_frame(kind: str, frames: Iterable[bytes]) -> bytes:
    """Build one length-prefixed reconcile *envelope* frame of ``kind``.

    ``kind`` is one of :data:`RECON_REQ` / :data:`RECON_RANGE` /
    :data:`RECON_RESULT`. ``frames`` is the ordered batch of opaque reconcile
    frame bytes for this round (each a ``reconcile-probe`` / ``reconcile-leaf``
    frame built by :mod:`knitweb.p2p.reconcile`). The batch may be empty — an
    empty :data:`RECON_RESULT` is exactly how the responder signals convergence.
    """
    if kind not in (RECON_REQ, RECON_RANGE, RECON_RESULT):
        raise InventoryError(f"not a reconcile envelope kind: {kind!r}")
    return wire.write_frame_bytes(
        {"kind": kind, "frames": _check_recon_batch(frames)}
    )


def parse_recon_frame(frame: bytes) -> Tuple[str, List[bytes]]:
    """Parse a reconcile envelope -> ``(kind, [reconcile frame bytes, ...])``."""
    msg = wire.read_frame_bytes(frame)
    kind = msg.get("kind")
    if kind not in (RECON_REQ, RECON_RANGE, RECON_RESULT):
        raise InventoryError("not a reconcile envelope frame")
    frames = msg.get("frames")
    if not isinstance(frames, list):
        raise InventoryError("reconcile envelope frames must be a list")
    return kind, _check_recon_batch(frames)


# ---------------------------------------------------------------------------
# InventoryRelay — the announce/want state machine
# ---------------------------------------------------------------------------

# A store lookup: given a CID, return the *stored frame bytes verbatim* (the
# length-prefixed wire frame originally received/built for that record), or
# ``None`` if this node does not hold it. Returning stored bytes — rather than
# re-encoding a record — is what preserves signed-record byte-identity end to
# end: the bytes a peer receives are the bytes that were signed.
FrameLookup = Callable[[str], "bytes | None"]


class InventoryRelay:
    """Drives the inv -> getdata -> record exchange for one node.

    The relay holds:

      * a :class:`SeenSet` of CIDs it has announced or learned (dedup), and
      * a ``FrameLookup`` into the node's frame store (the verbatim, signed bytes
        for each held record).

    It is socket-free: every method consumes/produces ``bytes`` frames. A node
    adopts it by (a) calling :meth:`announce` for newly-woven CIDs and writing
    the returned frame to peers, (b) feeding inbound ``inv`` frames to
    :meth:`on_inv` and writing back the returned ``getdata`` frame, (c) feeding
    inbound ``getdata`` frames to :meth:`on_getdata` and writing back the
    returned record frames, and (d) feeding inbound record frames to
    :meth:`on_record` to mark them seen. None of that touches the in-flight core
    files.
    """

    def __init__(
        self,
        lookup: FrameLookup,
        *,
        seen: SeenSet | None = None,
    ) -> None:
        if not callable(lookup):
            raise TypeError("lookup must be callable")
        self._lookup = lookup
        self.seen = seen if seen is not None else SeenSet()

    # -- outbound announce ------------------------------------------------

    def announce(self, cids: Iterable[str]) -> "bytes | None":
        """Mark ``cids`` seen and build an ``inv`` frame for the *new* ones.

        Returns ``None`` when nothing is new to announce (every CID was already
        seen), so the caller skips the send entirely — this is the redundant
        traffic the SeenSet exists to cut. CIDs already seen are not re-announced.
        """
        fresh = [cid for cid in self.seen.filter_unseen(cids)]
        # filter_unseen is read-only; commit the announced CIDs to the seen-set so
        # we don't re-announce them on the next weave.
        self.seen.add_many(fresh)
        if not fresh:
            return None
        return build_inv_frame(fresh)

    # -- inbound inv -> outbound getdata ----------------------------------

    def on_inv(self, frame: bytes) -> "bytes | None":
        """Handle an inbound ``inv``; build a ``getdata`` for CIDs we lack.

        A CID is *wanted* iff we neither already hold its frame (lookup hits) nor
        have it in our seen-set. Wanting a CID marks it seen so a duplicate ``inv``
        from another peer in the same round does not trigger a second want — the
        O(diff) property. Returns ``None`` when we already have everything.
        """
        announced = parse_inv_frame(frame)
        want: List[str] = []
        for cid in self.seen.filter_unseen(announced):
            if self._lookup(cid) is not None:
                # Already stored (e.g. learned out of band); record as seen but
                # don't request it again.
                self.seen.add(cid)
                continue
            want.append(cid)
            self.seen.add(cid)
        if not want:
            return None
        return build_getdata_frame(want)

    # -- inbound getdata -> outbound record frames ------------------------

    def on_getdata(self, frame: bytes) -> List[bytes]:
        """Handle an inbound ``getdata``; return stored frames for held CIDs.

        Each returned element is the **stored frame bytes verbatim** (the exact
        signed wire frame), never a re-encode — so a record's CID is unchanged by
        a relay hop. CIDs we do not hold are silently skipped (the peer may want
        an item we never received); we never fabricate a body.
        """
        wanted = parse_getdata_frame(frame)
        out: List[bytes] = []
        for cid in wanted:
            stored = self._lookup(cid)
            if stored is None:
                continue
            if not isinstance(stored, (bytes, bytearray)):
                raise InventoryError("frame lookup must return bytes or None")
            out.append(bytes(stored))
        return out

    # -- inbound record ---------------------------------------------------

    def on_record(self, cid: str) -> bool:
        """Mark a delivered record's CID seen. Return ``True`` iff newly seen.

        Called after a peer answers our ``getdata`` and we have stored the frame
        (the caller verifies the signature and that the frame's CID matches the
        wanted CID before calling this). Marking it seen prevents us from
        re-requesting it on a future ``inv``.
        """
        return self.seen.add(cid)
