"""Fabric item types: KnowledgeItem, ResourceItem, FabricCheckpoint.

These are the first-class node schemas that spiders weave into the Web.

  * KnowledgeItem  — a content-addressed piece of knowledge (fact, document,
                     annotation). The building block of the knowledge layer.
  * ResourceItem   — a resource offer published by a spider: GPU compute, CPU,
                     storage, or any bounded-capacity service. Price and capacity
                     are integers (PLS-wei per epoch; no floats).
  * FabricCheckpoint — a snapshot of the Web's state (a root committing to both
                       node CIDs AND edges) anchored to a Pulse Beat.  Checkpoints are woven into
                       the Web itself so their CIDs chain the fabric's history.

All three are frozen dataclasses that round-trip through canonical CBOR, so their
CIDs are deterministic and collision-free across peers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import canonical, crypto
from ..core.pulse import Beat
from .web import Web

__all__ = [
    "KnowledgeItem",
    "ResourceItem",
    "FabricCheckpoint",
    "web_state_root",
    "checkpoint",
]


def _require_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int")


# ---------------------------------------------------------------------------
# KnowledgeItem
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KnowledgeItem:
    """A content-addressed knowledge node on the fabric.

    Tags are stored sorted so canonical encoding is independent of insertion
    order (CBOR arrays are ordered; determinism requires a canonical sort).
    """

    title: str
    body: str
    author: str       # PLS address of the publishing spider
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_record(self) -> dict:
        return {
            "kind": "knowledge",
            "title": self.title,
            "body": self.body,
            "author": self.author,
            "tags": sorted(self.tags),
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())

    def weave(self, web: Web) -> str:
        """Weave this item into *web*; return its CID."""
        return web.weave(self.to_record())


# ---------------------------------------------------------------------------
# ResourceItem
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceItem:
    """A resource-offer node: bounded hardware capacity published by a spider.

    ``capacity`` and ``price_per_epoch`` are integer PLS-wei quantities.
    ``resource_kind`` is an open string (e.g. "gpu", "cpu", "storage").
    """

    resource_kind: str   # "gpu" | "cpu" | "storage" | ...
    capacity: int        # integer units of the resource
    price_per_epoch: int # PLS-wei per Pulse epoch
    provider: str        # PLS address of the offering spider

    def __post_init__(self) -> None:
        _require_int("capacity", self.capacity)
        _require_int("price_per_epoch", self.price_per_epoch)
        if self.capacity < 0:
            raise ValueError("capacity must be non-negative")
        if self.price_per_epoch < 0:
            raise ValueError("price_per_epoch must be non-negative")

    def to_record(self) -> dict:
        return {
            "kind": "resource",
            "resource_kind": self.resource_kind,
            "capacity": self.capacity,
            "price_per_epoch": self.price_per_epoch,
            "provider": self.provider,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())

    def weave(self, web: Web) -> str:
        """Weave this item into *web*; return its CID."""
        return web.weave(self.to_record())


# ---------------------------------------------------------------------------
# FabricCheckpoint
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FabricCheckpoint:
    """A snapshot of the Web's state anchored to a Pulse Beat.

    ``state_root`` is the hex-encoded SHA-256 root committing to both the node
    CIDs AND the edges of the Web at checkpoint time.  Weaving the checkpoint
    into the Web itself creates an auditable, content-addressed history of
    fabric evolution.
    """

    epoch: int
    beat_cid: str   # CID of the Pulse Beat this checkpoint is anchored to
    state_root: str # root committing to Web node CIDs AND edges (hex)
    node_count: int
    edge_count: int

    def __post_init__(self) -> None:
        _require_int("epoch", self.epoch)
        _require_int("node_count", self.node_count)
        _require_int("edge_count", self.edge_count)

    def to_record(self) -> dict:
        return {
            "kind": "fabric-checkpoint",
            "epoch": self.epoch,
            "beat_cid": self.beat_cid,
            "state_root": self.state_root,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())

    def weave(self, web: Web) -> str:
        """Weave this checkpoint into *web*; return its CID."""
        return web.weave(self.to_record())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def web_state_root(web: Web) -> str:
    """Return the hex state root committing to the FULL web: nodes AND edges.

    The root is ``sha256(node_root || edge_root)`` where:

      * ``node_root`` is the SHA-256 Merkle root of the sorted node CIDs, and
      * ``edge_root`` is the SHA-256 Merkle root of the canonical-CBOR bytes of
        every edge's ``to_record()``, with edges in a total canonical order by
        ``(src, rel, dst, weight)`` — the same ordering Web.traverse relies on.

    Committing to edges (not just nodes) means two Webs with identical node sets
    but different relations, weights, or links produce *different* roots, so edge
    divergence is visible to the root and binding under an OriginTrail anchor.
    Each empty side hashes to sha256(b""), the canonical empty-set sentinel, and
    both sides use canonical, float-free, integer-weight encoding, so every peer
    with the same nodes AND edges produces the same root regardless of insertion
    order. The result is always 64 hex chars.
    """
    sorted_cids = sorted(web.nodes.keys())
    # Hash each CID into a fixed 32-byte leaf so the root is always a real digest
    # (otherwise a single-node Web would return the raw CID bytes unchanged).
    node_leaves = [crypto.sha256(cid.encode("utf-8")) for cid in sorted_cids]
    node_root = crypto.merkle_root(node_leaves)

    # Total canonical order over edges matching traverse's (rel, dst) tie-break,
    # extended to a full (src, rel, dst, weight) key so it is a deterministic
    # total order across peers regardless of adjacency insertion order.
    all_edges = [edge for edges in web._out.values() for edge in edges]
    all_edges.sort(key=lambda e: (e.src, e.rel, e.dst, e.weight))
    edge_leaves = [crypto.sha256(canonical.encode(e.to_record())) for e in all_edges]
    edge_root = crypto.merkle_root(edge_leaves)

    return crypto.sha256(node_root + edge_root).hex()


def checkpoint(web: Web, beat: Beat) -> FabricCheckpoint:
    """Create a FabricCheckpoint from the current *web* state, anchored to *beat*."""
    root = web_state_root(web)
    n, e = web.size
    return FabricCheckpoint(
        epoch=beat.epoch,
        beat_cid=beat.cid,
        state_root=root,
        node_count=n,
        edge_count=e,
    )
