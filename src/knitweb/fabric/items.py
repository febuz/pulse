"""Fabric item types: KnowledgeItem, ResourceItem, FabricCheckpoint.

These are the first-class node schemas that spiders weave into the Web.

  * KnowledgeItem  — a content-addressed piece of knowledge (fact, document,
                     annotation). The building block of the knowledge layer.
  * ResourceItem   — a resource offer published by a spider: GPU compute, CPU,
                     storage, or any bounded-capacity service. Price and capacity
                     are integers (PLS-wei per epoch; no floats).
  * FabricCheckpoint — a snapshot of the Web's state (Merkle root of sorted node
                       CIDs) anchored to a Pulse Beat.  Checkpoints are woven into
                       the Web itself so their CIDs chain the fabric's history.

All three are frozen dataclasses that round-trip through canonical CBOR, so their
CIDs are deterministic and collision-free across peers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import canonical, crypto
from .web import Web

__all__ = [
    "KnowledgeItem",
    "ResourceItem",
    "FabricCheckpoint",
    "web_state_root",
    "checkpoint",
]


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

    ``state_root`` is the hex-encoded SHA-256 Merkle root of the sorted node
    CIDs at checkpoint time.  Weaving the checkpoint into the Web itself creates
    an auditable, content-addressed history of fabric evolution.
    """

    epoch: int
    beat_cid: str   # CID of the Pulse Beat this checkpoint is anchored to
    state_root: str # Merkle root of sorted Web node CIDs (hex)
    node_count: int
    edge_count: int

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
    """Return the hex Merkle root of the sorted node CIDs in *web*.

    An empty Web hashes to sha256(b""), the canonical empty-set sentinel.
    Sorting the CIDs before building the Merkle tree ensures every peer with
    the same set of nodes produces the same root regardless of insertion order.
    """
    sorted_cids = sorted(web.nodes.keys())
    # Hash each CID into a fixed 32-byte leaf so the root is always a real digest
    # (otherwise a single-node Web would return the raw CID bytes unchanged).
    leaves = [crypto.sha256(cid.encode("utf-8")) for cid in sorted_cids]
    return crypto.merkle_root(leaves).hex()


def checkpoint(web: Web, beat: "Beat") -> FabricCheckpoint:  # noqa: F821
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
