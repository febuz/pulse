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
from typing import Callable, Iterable, List, Mapping, Tuple

from ..core import canonical
from . import wire

__all__ = [
    "InventoryError",
    "SeenSet",
    "ServeBudget",
    "INV",
    "GETDATA",
    "RECON_REQ",
    "RECON_RANGE",
    "RECON_RESULT",
    "MAX_RECON_FRAMES",
    "MAX_GETDATA_BATCH",
    "SERVE_BYTES_PER_WINDOW",
    "SERVE_WINDOW_SECONDS",
    "INV_PROBE_CIDS_PER_WINDOW",
    "RECON_FRAMES_PER_WINDOW",
    "parse_recon_batch",
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

# ---------------------------------------------------------------------------
# Outbound serve budget — anti-amplification on the getdata/IWANT serve path (#91)
# ---------------------------------------------------------------------------
#
# The threat: the inv-getdata / mesh-IWANT serve path returns the *full stored
# body* for every CID a peer asks for, with no per-peer cap on count or bytes.
# A single ~2 MiB request (a getdata naming tens of thousands of CIDs — or a
# peer hammering the same whole-inventory request in a tight loop) can elicit
# hundreds of GiB served (~135,000x amplification): a classic reflected-DoS
# multiplier. The two caps below bound the OUTBOUND side so a request can never
# amplify past a fixed, integer budget, while staying generous enough that an
# honest Erlay reconcile of a realistic symmetric difference always completes.

# (a) Per-REQUEST record cap: a single getdata/IWANT serves at most this many
# stored bodies, no matter how many CIDs it names. An honest Erlay reconcile
# pulls exactly ``|diff|`` CIDs; a small-to-moderate diff fits well under this,
# while a pathological whole-inventory pull (up to MAX_CIDS_PER_FRAME = 50_000)
# is truncated. A legitimately larger diff still makes progress: the un-served
# CIDs are simply re-requested on the next reconcile round (the SeenSet keeps it
# O(remaining-diff)), so it paginates across requests rather than deadlocking.
MAX_GETDATA_BATCH = 2_048

# (a-frame) Per-RESPONSE aggregate-frame cap. ``on_getdata`` returns verbatim
# stored frames; the serve callers (``fabric/node.py``) DECODE each
# (``wire.read_frame_bytes``) and re-wrap them as ONE ``{kind:"inv-data",
# records:[...]}`` frame that must ITSELF encode under ``wire.MAX_FRAME_BYTES``
# (8 MiB). Neither the count cap above nor the per-peer byte budget below bounds
# THIS: up to MAX_GETDATA_BATCH bodies — or a handful of ~MiB bodies — can sum
# past 8 MiB while each body is individually well under every per-body cap. The
# oversized wrapped frame then raises ``WireError`` at ``write_frame`` and the
# transport silently DROPS the connection (no response), permanently starving
# fetches of any held group summing > 8 MiB. Bound the aggregate of served-body
# bytes under MAX_FRAME_BYTES with headroom for the inv-data envelope + array /
# frame headers (~tens of bytes). Each decoded record re-embeds SMALLER than its
# stored frame (the inner frame header is stripped), so the sum of stored-frame
# lengths OVER-bounds the wrapped payload — 64 KiB of headroom is generous. The
# remainder paginates to the next reconcile round exactly like the count cap, so
# a large held group still converges across rounds rather than deadlocking.
MAX_SERVE_AGGREGATE_BYTES = wire.MAX_FRAME_BYTES - 64 * 1024  # 8 MiB - 64 KiB

# (b) Per-PEER byte budget over an integer time window: a token/byte bucket. A
# peer may be served at most ``SERVE_BYTES_PER_WINDOW`` body bytes per rolling
# ``SERVE_WINDOW_SECONDS`` window; a request that would exceed the remaining
# budget is served only up to what the budget allows (and the rest is dropped /
# deferred to a later window, NOT served). Sized GENEROUSLY for honest use — a
# moderate reconcile diff of a few thousand ~1 KiB records is a few MiB, far
# under the per-window allowance — while capping a hammering peer to a fixed
# bytes/window ceiling that kills the GiB-scale amplification. Integer-only; the
# clock is an injected monotonic integer (seconds), never a wall-clock.
#
# LIVENESS FLOOR — this MUST stay >= ``wire.MAX_FRAME_BYTES``. ``ServeBudget.take``
# is all-or-nothing (#189): a single body larger than the WHOLE window budget is
# deferred every window and never served — permanent fetch starvation, not mere
# throttling. Safe only because the largest admissible frame (``wire.MAX_FRAME_BYTES``,
# 8 MiB) fits inside one window (256 MiB). Shrinking this below MAX_FRAME_BYTES — or
# raising MAX_FRAME_BYTES above it — silently starves large-record fetches. Pinned by
# tests/property/test_inventory.py::test_serve_window_covers_the_largest_possible_body_so_no_fetch_can_starve (#195).
SERVE_BYTES_PER_WINDOW = 256 * 1024 * 1024  # 256 MiB / window / peer
SERVE_WINDOW_SECONDS = 10

# (c) Per-PEER inv-announce PROBE budget over the same integer window (#146). The
# inv-announce reply (``_serve_inv``) is a deterministic function of the node's
# holdings: the CIDs it does NOT hold come back as the want list, so the announcer
# learns the EXACT held/lacked partition of every CID it named — a holdings /
# membership oracle. on_getdata's byte budget (#91) does not gate THIS reply (no
# body travels), so a prober could enumerate the full holdings set for free by
# announcing arbitrary candidate CIDs. We reuse the same ServeBudget token-bucket
# primitive, but here the token unit is ONE PROBED CID (the discriminating unit
# the oracle leaks), so the per-peer cap is on CIDs-probed-per-window rather than
# bytes. An honest reconcile/announce names a normal batch well under this cap and
# is served unchanged; a peer that floods candidate CIDs to read out the partition
# is cut off once it exhausts the window (its reply is withheld — served as a
# non-discriminating inv-ack — so beyond the budget it learns nothing). Sized to
# admit honest batches (a full MAX_GETDATA_BATCH reconcile diff, with headroom)
# while a mass-enumeration sweep of distinct candidate CIDs exhausts it fast.
INV_PROBE_CIDS_PER_WINDOW = 4 * MAX_GETDATA_BATCH  # 8192 probed CIDs / window / peer

# (d) Per-PEER reconcile-frame budget over the same integer window (#159). The
# anti-entropy reconcile responder (``FabricNode._serve_recon``) drives a
# bisection session whose per-probe cost is O(in-range inventory) — each probe
# SHA-256-hashes every held CID in its range (``reconcile.cid_fingerprint``). That
# is the one serve path with NO budget: only ``wire.MAX_FRAME_BYTES`` bounds the
# envelope, so a single 8 MiB request can smuggle ~95k full-keyspace probes and
# burn minutes of CPU (a HIGH CPU-amplification DoS). Reconcile is designed for
# NEAR-SYNCED peers (O(diff) frames, a handful of rounds); a from-empty node syncs
# via inv-announce/getdata, not reconcile. So a per-window cap at one getdata batch
# is generous for honest sessions yet cuts off a probe flood. Token unit = one
# reconcile FRAME (the per-probe hashing unit), reusing the ServeBudget primitive
# exactly as the #91 byte budget and the #146 inv-probe CID budget do.
RECON_FRAMES_PER_WINDOW = MAX_GETDATA_BATCH  # 2048 reconcile frames / window / peer


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
# ServeBudget — per-peer outbound byte bucket over an integer time window (#91)
# ---------------------------------------------------------------------------

class ServeBudget:
    """A per-peer token/byte bucket that caps OUTBOUND served bytes per window.

    This is the anti-amplification governor on the serve side of the lazy relay.
    Each peer is allotted ``bytes_per_window`` body bytes per rolling
    ``window_seconds`` window; :meth:`take` debits a peer's bucket by the bytes
    about to be served and returns how many bytes the budget *permits* right now
    (which may be less than requested, or zero when the peer is exhausted). The
    serve path serves only up to the permitted amount and drops/defers the rest —
    so a request can never amplify past a fixed integer ceiling, no matter how
    many bodies it names or how hard the peer hammers.

    Determinism: the only notion of time is an injected **monotonic integer
    clock** (whole seconds); there is no wall-clock and no randomness, so a
    replayed request sequence debits identically. The window is a hard integer
    boundary (``now // window_seconds``): crossing into a new window refills the
    bucket to full. Per-peer state is bounded to the most-recently-active peers
    by an integer ``max_peers`` LRU so a flood of distinct peer keys cannot leak
    unbounded buckets.
    """

    def __init__(
        self,
        *,
        bytes_per_window: int = SERVE_BYTES_PER_WINDOW,
        window_seconds: int = SERVE_WINDOW_SECONDS,
        max_peers: int = 4_096,
        clock: "Callable[[], int] | None" = None,
    ) -> None:
        for name, val in (
            ("bytes_per_window", bytes_per_window),
            ("window_seconds", window_seconds),
            ("max_peers", max_peers),
        ):
            if not isinstance(val, int) or isinstance(val, bool):
                raise TypeError(f"{name} must be int")
            if val < 1:
                raise ValueError(f"{name} must be >= 1")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable or None")
        self._bytes_per_window = bytes_per_window
        self._window_seconds = window_seconds
        self._max_peers = max_peers
        # The default clock is a monotonic integer second counter. Tests inject a
        # virtual clock so the window boundary is deterministic with no real time.
        self._clock = clock if clock is not None else _default_monotonic_seconds
        # peer key -> (window index it was last refilled in, bytes remaining).
        self._buckets: "OrderedDict[str, Tuple[int, int]]" = OrderedDict()

    @property
    def bytes_per_window(self) -> int:
        return self._bytes_per_window

    @property
    def window_seconds(self) -> int:
        return self._window_seconds

    def _window_index(self) -> int:
        now = self._clock()
        if not isinstance(now, int) or isinstance(now, bool):
            raise TypeError("clock must return int seconds")
        # Floor-divide into integer windows; negative clocks floor toward -inf,
        # which is still monotonic and deterministic.
        return now // self._window_seconds

    def remaining(self, peer: str) -> int:
        """Bytes ``peer`` may still be served in the CURRENT window (read-only)."""
        win = self._window_index()
        rec = self._buckets.get(peer)
        if rec is None or rec[0] != win:
            return self._bytes_per_window
        return rec[1]

    def take(self, peer: str, want_bytes: int) -> int:
        """Debit ``peer``'s bucket *all-or-nothing*; return ``want_bytes`` if it
        fit this window, else 0.

        Refills the bucket to full on crossing into a new integer window. A
        request is granted whole or not at all: if ``want_bytes`` exceeds the
        remaining budget, NOTHING is debited and 0 is returned — so an over-limit
        request never burns the budget an honest peer needs for the smaller,
        affordable requests that follow it in the same window. ``want_bytes`` of 0
        is a no-op that returns 0 (and still touches LRU recency for the peer).

        No caller can act on a *partial* byte/frame/probe grant — you cannot serve
        half a body, half a reconcile frame, or half a probe reply, so the work is
        either done in full or deferred to the next window. A ``min(want, remaining)``
        partial debit was therefore pure over-charge: it zeroed the bucket for a
        request that was then rejected wholesale, starving the peer of subsequent
        affordable service (the #185 over-debit class — also present in the
        ``fabric/node.py`` ingest/probe/recon serve gates, which read this value as
        an all-or-nothing ``take(n) < n`` test and are fixed transitively here).
        """
        if not isinstance(peer, str) or not peer:
            raise InventoryError("peer key must be a non-empty str")
        if not isinstance(want_bytes, int) or isinstance(want_bytes, bool):
            raise TypeError("want_bytes must be int")
        if want_bytes < 0:
            raise ValueError("want_bytes must be >= 0")
        win = self._window_index()
        rec = self._buckets.get(peer)
        if rec is None or rec[0] != win:
            remaining = self._bytes_per_window
        else:
            remaining = rec[1]
        granted = want_bytes if want_bytes <= remaining else 0
        self._buckets[peer] = (win, remaining - granted)
        self._buckets.move_to_end(peer)
        if len(self._buckets) > self._max_peers:
            self._buckets.popitem(last=False)
        return granted


def _default_monotonic_seconds() -> int:
    """Prod clock: a monotonic integer-second counter (no wall-clock).

    ``time.monotonic_ns`` is monotonic and unaffected by clock adjustments; we
    truncate to whole seconds so the budget's window is a pure integer. Imported
    lazily so the module's import graph stays free of a top-level ``time`` on the
    socket-free, deterministic core paths that never instantiate a live budget.
    """
    import time

    return time.monotonic_ns() // 1_000_000_000


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


def parse_recon_batch(frames: object) -> List[bytes]:
    """Validate an INBOUND reconcile batch off the wire (#159).

    The responder reads ``msg["frames"]`` straight off an untrusted carrier; this
    enforces the same ``MAX_RECON_FRAMES`` cap the *builder* assumes, so an 8 MiB
    envelope can no longer smuggle ~95k probes past the responder's per-probe
    O(inventory) hashing. Raises :class:`InventoryError` (a ``ValueError``) on a
    non-list batch, non-bytes entry, or over-cap count — which the shared
    ``_dispatch`` already maps to a ``bad-request`` with no new error wiring.
    """
    if not isinstance(frames, list):
        raise InventoryError("reconcile frames must be a list")
    return _check_recon_batch(frames)


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
#
# Per-hop byte-identity, scoped precisely (#76, #53): "byte-identical across a
# hop" covers exactly (a) the embedded signed-record bytes and its CID — the
# record travels verbatim, so ``record_cid(record) == canonical.cid(record)`` is
# unchanged at every node and no body can be forged or re-encoded — and (b) the
# stripped carried map (the business payload after transport-only keys are
# removed). It does NOT cover the per-hop *transport envelope*: each relayer
# re-signs the outer frame under its OWN key (author/sig differ per relayer) and
# adds transport-only ``_relay_*`` keys, which are stripped before any
# signed/business logic runs (#53). So "byte-identity" must not be misread as
# preserving the outer envelope's signature across a hop — only the nested signed
# record (whose author signature lives inside the preserved body) and the
# stripped map are byte-stable. Record authenticity is intact precisely because
# the author signature rides inside that preserved body, not the re-signed
# envelope.
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
        budget: "ServeBudget | None" = None,
    ) -> None:
        if not callable(lookup):
            raise TypeError("lookup must be callable")
        self._lookup = lookup
        self.seen = seen if seen is not None else SeenSet()
        # Outbound anti-amplification governor (#91). A per-peer byte bucket over
        # an integer window; ``on_getdata`` debits it before returning bodies so a
        # single request / a hammering peer can never amplify past a fixed budget.
        # Default-constructed (prod monotonic clock) unless a test injects one.
        self.budget = budget if budget is not None else ServeBudget()

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

    def on_getdata(self, frame: bytes, *, peer: str | None = None) -> List[bytes]:
        """Handle an inbound ``getdata``; return stored frames for held CIDs.

        Each returned element is the **stored frame bytes verbatim** (the exact
        signed wire frame), never a re-encode — so a record's CID is unchanged by
        a relay hop. CIDs we do not hold are silently skipped (the peer may want
        an item we never received); we never fabricate a body.

        Anti-amplification (#91): the serve is bounded on THREE axes so a single
        request can never reflect an unbounded body multiple back at the requester.

          * **per-request count** — at most :data:`MAX_GETDATA_BATCH` bodies are
            returned, no matter how many CIDs the frame names. The excess CIDs are
            simply not served on this request; an honest peer re-requests the
            remaining diff on its next reconcile round (the SeenSet keeps that
            O(remaining-diff)), so a legitimately large diff paginates across
            requests rather than amplifying or deadlocking.
          * **per-response aggregate frame** — the bodies are wrapped by the serve
            callers into ONE ``inv-data`` frame bounded by ``wire.MAX_FRAME_BYTES``.
            Serving is stopped before a body would push the cumulative served bytes
            past :data:`MAX_SERVE_AGGREGATE_BYTES` (< MAX_FRAME_BYTES), so the
            wrapped frame always encodes — but at least one body is always served
            (a single held CID can never be permanently deferred). The rest
            paginates to the next round like the count cap.
          * **per-peer bytes/window** — when ``peer`` is supplied, each body's
            bytes are debited from that peer's :class:`ServeBudget` bucket; once
            the peer's window budget is exhausted, no further bodies are served
            this window (they are dropped/deferred, NOT served). ``peer=None``
            (no identified sender) skips only the byte bucket — the per-request
            count cap still applies — so an unidentifiable carrier cannot use
            anonymity to bypass the hard count ceiling.

        The byte budget is checked *before* a body is appended, so the returned
        list never exceeds the peer's remaining budget; verbatim bytes are
        otherwise untouched, so byte-identity is preserved exactly.
        """
        wanted = parse_getdata_frame(frame)
        out: List[bytes] = []
        aggregate = 0  # cumulative served-body bytes (for the inv-data frame cap)
        for cid in wanted:
            if len(out) >= MAX_GETDATA_BATCH:
                # Per-request count cap reached: stop serving. Remaining CIDs are
                # re-requested next round (O(remaining-diff)); nothing deadlocks.
                break
            stored = self._lookup(cid)
            if stored is None:
                continue
            if not isinstance(stored, (bytes, bytearray)):
                raise InventoryError("frame lookup must return bytes or None")
            body = bytes(stored)
            # Per-response aggregate-frame cap: the caller wraps these bodies into
            # ONE inv-data frame bounded by wire.MAX_FRAME_BYTES. Stop before a body
            # would push the aggregate past MAX_SERVE_AGGREGATE_BYTES (deferring the
            # rest to the next round) — but always serve at least one body so a
            # single held CID is never permanently starved. Checked BEFORE the byte
            # budget so a deferred body does not burn the peer's window budget.
            if out and aggregate + len(body) > MAX_SERVE_AGGREGATE_BYTES:
                break
            if peer is not None:
                # Debit the per-peer byte bucket. ``take`` returns how many bytes
                # the budget permits right now; if it cannot cover this whole
                # body, the peer is out of budget for this window — stop serving
                # (defer the rest to a later window) rather than partial-serving a
                # frame, which would corrupt byte-identity.
                if self.budget.take(peer, len(body)) < len(body):
                    break
            out.append(body)
            aggregate += len(body)
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
