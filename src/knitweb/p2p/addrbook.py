"""Source-group bucketed address book — eclipse resistance for peer discovery.

:mod:`knitweb.p2p.discovery`'s :class:`~knitweb.p2p.discovery.PeerDirectory` is a
single flat dict and its ``sample()`` returns the first-``k`` peers *by sort
order*. That makes the node trivially eclipsable: an attacker who floods PEX with
thousands of attacker-controlled addresses (all routing back to a handful of
attacker hosts) fills the directory, and because selection is by raw count /
lexical order the honest peers are crowded out — every peer the node then talks to
is the attacker's. An eclipsed node sees only the attacker's view, which defeats
the equivocation gossip, anti-entropy self-healing, and reputation layers that all
assume *some* honest peer is reachable.

This module ports **Bitcoin Core's addrman new/tried bucketing** (the classic
eclipse-resistance design) faithfully but idiomatically to pure-Python stdlib:

  * Two tables. **new** holds addresses we have only *heard about* (via PEX);
    **tried** holds addresses we have actually *connected to* successfully. A
    successful contact promotes new -> tried.
  * Bucketing by **diversity, not count**. A learned address lands in a bucket
    chosen by ``H(secret || source_group || addr_group) mod N_NEW`` — keyed on
    *both* who told us about it (the source group) and its own ``/16`` address
    group. A connected address lands in ``H(secret || addr_group) mod N_TRIED``.
    Each bucket then picks a deterministic slot for the address. Because slots are
    bounded, one source group (or one ``/16``) can occupy only a bounded fraction
    of the table no matter how many addresses it injects — address *diversity*,
    not raw flood volume, governs what survives and what gets sampled.
  * The bucketing key is salted by a **per-node secret**. The secret lives entirely
    off the hashed/signed-record path: it never enters a canonical-CBOR record, a
    Knit, a signature, or a CID. It only perturbs *local, in-memory* bucket
    placement so an attacker cannot pre-compute which buckets their addresses will
    land in (the secret is unknown to them) and so cannot craft addresses that
    collide into a single victim bucket. Canonical bytes are therefore untouched
    and every Knit CID is unchanged — :mod:`tests.property.test_addrbook` asserts
    this directly.

Everything is integer/bytes only, fully bounded, and deterministic: the per-node
secret and any RNG are *injected*, so the same inputs always produce the same
buckets and the same sample. The address-group extraction mirrors Bitcoin Core's
"group by routability prefix" (``/16`` for IPv4, ``/32`` for IPv6, the whole host
for anything non-numeric such as a relay mailbox or .onion-style name).

The module **wraps** discovery rather than editing it: it ingests the same
:class:`PeerAddress` values, and exposes a bucketed :meth:`AddrBook.sample` the
bootstrap loop can call in place of ``PeerDirectory.sample``. It does not touch
``discovery.py``'s PEX core, so the two can coexist while the node migrates.
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass

from .transport import PeerAddress

__all__ = [
    "DEFAULT_NEW_BUCKETS",
    "DEFAULT_TRIED_BUCKETS",
    "DEFAULT_BUCKET_SIZE",
    "address_group",
    "source_group",
    "AddrBook",
]

# Table geometry. Mirrors Bitcoin Core's split (it uses 1024/256 x 64); we keep the
# *shape* — many small buckets — at knitweb scale. Every value is a hard cap, so the
# whole structure is bounded by NEW_BUCKETS*BUCKET_SIZE + TRIED_BUCKETS*BUCKET_SIZE
# entries regardless of how much an attacker floods.
DEFAULT_NEW_BUCKETS = 256
DEFAULT_TRIED_BUCKETS = 64
DEFAULT_BUCKET_SIZE = 8

# Domain tags fold into the bucket hash so the "new" and "tried" keyings, and the
# slot derivation, occupy disjoint hash spaces. These are local-only mixing bytes;
# they never enter a signed/canonical record.
_TAG_NEW = b"knitweb-addrbook-new:v1"
_TAG_TRIED = b"knitweb-addrbook-tried:v1"
_TAG_SLOT = b"knitweb-addrbook-slot:v1"


def _norm_host(host: str) -> str:
    """Lower-cased, stripped host string for stable grouping/keying."""
    return (host or "").strip().lower()


def address_group(peer: PeerAddress) -> bytes:
    """The routability group of ``peer`` — its ``/16`` for IPv4, ``/32`` for IPv6.

    Bitcoin Core buckets by the network *group* an address belongs to rather than the
    exact address, because an attacker controlling one ``/16`` can mint 65 536 distinct
    IPv4 addresses but cannot cheaply span many groups. Collapsing to the group is what
    makes flooding expensive: distinct-but-same-group addresses share a group key and so
    compete for the same bounded slots.

    Non-numeric hosts (relay mailboxes, hostnames) have no IP prefix, so the whole
    normalised host is the group — two relay peers on different mailboxes stay distinct,
    but a thousand fake mailboxes still each form their own group (there is no IP scarcity
    to lean on there; the source-group keying in the *new* table is the defence for those).
    Returns raw bytes so it feeds straight into a hash with no float/encoding ambiguity.
    """
    host = _norm_host(peer.host)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal: group by transport + full host + sorted params so that
        # carrier-distinct endpoints (tcp vs relay, distinct mailboxes) never collapse.
        suffix = "".join(f";{k}={v}" for k, v in sorted(peer.params.items()))
        return f"name:{peer.transport}:{host}{suffix}".encode("utf-8")
    if ip.version == 4:
        # /16 group: top two octets.
        return b"ip4:" + ip.packed[:2]
    # IPv6 /32 group: top four bytes (Bitcoin Core groups IPv6 more coarsely than v4).
    return b"ip6:" + ip.packed[:4]


def source_group(source: "PeerAddress | None") -> bytes:
    """The group of the peer that *told us about* an address (the PEX source).

    Keying the *new* table on the source group is the heart of eclipse resistance: an
    attacker who relays thousands of addresses all share one (or few) source group(s),
    so every address they push competes for the bounded slots of the buckets that source
    group maps to — they cannot spray across all buckets. ``None`` (address we minted
    ourselves, e.g. from the static peerbook) maps to a fixed local group.
    """
    if source is None:
        return b"local:"
    return b"src:" + address_group(source)


def _u32(digest: bytes) -> int:
    """First 4 bytes of a digest as a big-endian unsigned int (deterministic, float-free)."""
    return int.from_bytes(digest[:4], "big")


def _h(secret: bytes, *parts: bytes) -> bytes:
    """SHA-256 over secret || each length-framed part. Length-framing makes the
    concatenation injective so ``(a, b)`` and ``(ab, '')`` cannot collide."""
    m = hashlib.sha256()
    m.update(len(secret).to_bytes(4, "big"))
    m.update(secret)
    for p in parts:
        m.update(len(p).to_bytes(4, "big"))
        m.update(p)
    return m.digest()


@dataclass(frozen=True)
class _Entry:
    peer: PeerAddress
    src_group: bytes  # source group recorded at insertion (for new-table bucketing)


class AddrBook:
    """A source-group/address-group bucketed address book over :class:`PeerAddress`.

    Construct with an injected per-node ``secret`` (raw bytes, kept local — never
    hashed into any record). All placement is deterministic in ``secret`` + inputs,
    so tests reproduce buckets exactly. Adopt it by calling :meth:`add_new` from the
    PEX merge path, :meth:`mark_tried` after a successful dial, and :meth:`sample`
    where the bootstrap loop currently calls ``PeerDirectory.sample``.
    """

    def __init__(
        self,
        secret: bytes,
        *,
        new_buckets: int = DEFAULT_NEW_BUCKETS,
        tried_buckets: int = DEFAULT_TRIED_BUCKETS,
        bucket_size: int = DEFAULT_BUCKET_SIZE,
    ) -> None:
        if not isinstance(secret, (bytes, bytearray)):
            raise TypeError("secret must be bytes (a local, off-record per-node salt)")
        if min(new_buckets, tried_buckets, bucket_size) < 1:
            raise ValueError("bucket counts and size must be >= 1")
        self._secret = bytes(secret)
        self._n_new = new_buckets
        self._n_tried = tried_buckets
        self._size = bucket_size
        # bucket index -> {slot index -> _Entry}; sparse, only populated slots stored.
        self._new: dict[int, dict[int, _Entry]] = {}
        self._tried: dict[int, dict[int, _Entry]] = {}
        # Fast membership / promotion bookkeeping, keyed by the same carrier-aware key
        # discovery uses, so an address is "the same address" across both layers.
        self._in_tried: set[str] = set()
        self._in_new: set[str] = set()

    # -- keying ---------------------------------------------------------------

    @staticmethod
    def _peer_key(peer: PeerAddress) -> str:
        suffix = "".join(f";{k}={v}" for k, v in sorted(peer.params.items()))
        return f"{peer.transport}://{_norm_host(peer.host)}:{peer.port}{suffix}"

    def _new_bucket(self, peer: PeerAddress, src_group: bytes) -> int:
        return _u32(_h(self._secret, _TAG_NEW, src_group, address_group(peer))) % self._n_new

    def _tried_bucket(self, peer: PeerAddress) -> int:
        return _u32(_h(self._secret, _TAG_TRIED, address_group(peer))) % self._n_tried

    def _slot(self, bucket: int, peer: PeerAddress) -> int:
        key = self._peer_key(peer).encode("utf-8")
        return _u32(_h(self._secret, _TAG_SLOT, bucket.to_bytes(4, "big"), key)) % self._size

    # -- mutation -------------------------------------------------------------

    def add_new(self, peer: PeerAddress, source: "PeerAddress | None" = None) -> bool:
        """Record an address heard via PEX from ``source``. Returns True if it took a
        slot (a fresh address in a free/replaceable slot), False if it was crowded out
        or already tried.

        The deterministic slot means a *repeat* address from the same source maps to
        the same slot and simply refreshes it (idempotent), while a different address
        colliding into an occupied slot does NOT evict the incumbent — so an attacker
        cannot displace an honest entry already in a bucket by brute-forcing collisions
        (test-before-evict lineage: we never drop a known-good entry for an unproven one).
        """
        if not isinstance(peer, PeerAddress):
            raise TypeError("peer must be a PeerAddress")
        key = self._peer_key(peer)
        if key in self._in_tried:
            return False  # already proven; new table would only demote it
        src_grp = source_group(source)
        bucket = self._new_bucket(peer, src_grp)
        slot = self._slot(bucket, peer)
        slots = self._new.setdefault(bucket, {})
        incumbent = slots.get(slot)
        if incumbent is not None and self._peer_key(incumbent.peer) != key:
            # Slot taken by a *different* address — do not evict. Diversity cap reached.
            return False
        slots[slot] = _Entry(peer=peer, src_group=src_grp)
        self._in_new.add(key)
        return True

    def mark_tried(self, peer: PeerAddress) -> bool:
        """Promote ``peer`` to the *tried* table after a successful contact.

        Returns True if it occupies a tried slot afterwards. If the target tried slot is
        held by a *different* address we keep the incumbent (test-before-evict: the
        proven incumbent is not displaced by another claimant); the caller may retry the
        incumbent's liveness out of band before any eviction policy is layered on.
        """
        if not isinstance(peer, PeerAddress):
            raise TypeError("peer must be a PeerAddress")
        key = self._peer_key(peer)
        bucket = self._tried_bucket(peer)
        slot = self._slot(bucket, peer)
        slots = self._tried.setdefault(bucket, {})
        incumbent = slots.get(slot)
        if incumbent is not None and self._peer_key(incumbent.peer) != key:
            return False
        slots[slot] = _Entry(peer=peer, src_group=b"tried:")
        self._in_tried.add(key)
        # Drop any new-table copy: an address is in at most one table at a time.
        if key in self._in_new:
            self._in_new.discard(key)
            self._drop_from_new(peer)
        return True

    def _drop_from_new(self, peer: PeerAddress) -> None:
        key = self._peer_key(peer)
        for bucket, slots in self._new.items():
            for slot, entry in list(slots.items()):
                if self._peer_key(entry.peer) == key:
                    del slots[slot]

    # -- queries --------------------------------------------------------------

    def __len__(self) -> int:
        return self._count(self._new) + self._count(self._tried)

    @staticmethod
    def _count(table: dict[int, dict[int, _Entry]]) -> int:
        return sum(len(slots) for slots in table.values())

    def new_count(self) -> int:
        return self._count(self._new)

    def tried_count(self) -> int:
        return self._count(self._tried)

    def __contains__(self, peer: PeerAddress) -> bool:
        key = self._peer_key(peer)
        return key in self._in_tried or key in self._in_new

    def _table_entries(self, table: dict[int, dict[int, _Entry]]) -> list[_Entry]:
        """All entries in a table in deterministic (bucket, slot) order."""
        out: list[_Entry] = []
        for bucket in sorted(table):
            slots = table[bucket]
            for slot in sorted(slots):
                out.append(slots[slot])
        return out

    def sample(self, k: "int | None" = None, *, tried_bias: bool = True) -> list[PeerAddress]:
        """A bucketed, diversity-spread subset of known peers to dial/advertise.

        Walks buckets *round-robin* (one entry per bucket per pass) rather than draining a
        bucket at a time, so the returned sample spreads across address/source groups: no
        single flooded group can dominate the first ``k`` even if it filled its own buckets.
        With ``tried_bias`` (default) proven (*tried*) peers are preferred over merely-heard
        (*new*) ones, matching Bitcoin Core's preference for addresses we have reached before.
        ``k=None`` returns everything (still in the diversity-spread order).

        Eclipse-defence layers, from strongest to narrowest:

        1. **Tried-promotion is the load-bearing defence** (``tried_bias=True``, the
           default).  Addresses we have *actually connected to* occupy the *tried* table and
           are emitted first, before any *new*-table entry.  A realistic multi-``/16`` attacker
           owning a full ``/8`` (256 distinct ``/16`` prefixes, each with a distinct source
           group) can saturate the *new* table's per-bucket slots across many buckets — the
           round-robin diversity guarantee is narrower there.  But the *tried* table is
           populated only by live contacts, so an attacker who has never successfully dialled
           in cannot occupy a tried slot.  Any honest peer we have connected to therefore
           survives in the tried table and beats every new-table attacker entry in ``sample``.
           Call :meth:`mark_tried` after every successful dial to activate this layer.

        2. **New-table diversity** (round-robin + per-bucket caps) limits the fraction of
           the sample a single source group or address group can occupy, but it is a
           *narrower* guarantee: a sufficiently diverse attacker (many ``/16`` prefixes,
           many source groups) can still fill large parts of the new table.  The new-table
           guarantee is "no single ``/16`` dominates"; it is not "honest peers survive
           against an attacker spanning many ``/16``s."

        Therefore: if the node has any tried peers, those are always presented first and
        form the eclipse-resistant core; the new table is a discovery supplement, not the
        primary safety net.
        """
        ordered = self._round_robin(self._tried) if tried_bias else []
        if tried_bias:
            ordered += self._round_robin(self._new)
        else:
            ordered = self._round_robin(self._new) + self._round_robin(self._tried)
        peers = [e.peer for e in ordered]
        return peers if k is None else peers[: max(0, k)]

    def _round_robin(self, table: dict[int, dict[int, _Entry]]) -> list[_Entry]:
        """Flatten a table by visiting one slot from each bucket per pass.

        This is what converts "bounded per bucket" into "diverse in the sample": across
        the first N picks we touch up to N distinct buckets (hence distinct group keys)
        before ever taking a second entry from any one bucket.
        """
        buckets = sorted(table)
        # Snapshot each bucket's slots in slot order.
        columns = [[table[b][s] for s in sorted(table[b])] for b in buckets]
        out: list[_Entry] = []
        depth = max((len(c) for c in columns), default=0)
        for d in range(depth):
            for c in columns:
                if d < len(c):
                    out.append(c[d])
        return out

    def known(self) -> list[PeerAddress]:
        """All known peers (tried then new), diversity-spread — convenience alias."""
        return self.sample(None)
