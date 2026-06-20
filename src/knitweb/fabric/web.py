"""Web — the woven global graph (one of the seven core primitives).

The Web is the fabric that spiders crawl and build: a content-addressed graph of
typed **nodes** (any canonical record, identified by its CID) and first-class
**edges** (typed, weighted, directional links between CIDs). Knowledge items,
resource offers, work receipts, and Pulse beats all live here as nodes.

The Web is deliberately generic: it stores *content-addressed records* and the
relationships between them, with no knowledge of PLS/Fiber ledger semantics. The ledger
(braids/knits) and the fabric item schemas weave *into* the Web; domain knitwebs add
their own node and edge types at the edges. This keeps the shared ontology minimal
(KnitNet principle 10) while making the graph queryable and composable.

In the MVP this is an in-memory weave with deterministic traversal. The P2P layer
later feeds peer records into the same interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import canonical

__all__ = ["Web", "Edge"]


@dataclass(frozen=True)
class Edge:
    """A typed, weighted, directional link between two content-addressed nodes."""

    src: str        # CID of the source node
    dst: str        # CID of the destination node
    rel: str        # relation type, e.g. "supports", "custody", "produced-by"
    weight: int = 1 # integer weight (no floats; canonical-friendly)

    def to_record(self) -> dict:
        return {
            "kind": "edge",
            "src": self.src,
            "dst": self.dst,
            "rel": self.rel,
            "weight": self.weight,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())


@dataclass
class Web:
    """An in-memory woven graph of content-addressed nodes and typed edges."""

    nodes: dict[str, dict] = field(default_factory=dict)
    # adjacency: src_cid -> list of Edge
    _out: dict[str, list[Edge]] = field(default_factory=dict)
    _in: dict[str, list[Edge]] = field(default_factory=dict)
    _edge_metadata: dict[str, dict[str, object]] = field(default_factory=dict)

    @staticmethod
    def _validate_metadata_value(value: object) -> bool:
        return isinstance(value, (str, int, bool, float))

    def _normalize_edge_metadata(self, metadata: object) -> dict[str, object]:
        if metadata is None:
            return {}
        if not isinstance(metadata, dict):
            raise TypeError("edge metadata must be a dict")
        out: dict[str, object] = {}
        for key, value in metadata.items():
            if not isinstance(key, str) or not key:
                raise ValueError("edge metadata keys must be non-empty str")
            if not self._validate_metadata_value(value):
                raise TypeError(
                    "edge metadata values must be int, str, bool, or float"
                )
            out[key] = value
        return out

    def _edge_exists(self, src: str, dst: str, rel: str, weight: int) -> bool:
        candidate = Edge(src, dst, rel, weight)
        return any(e.cid == candidate.cid for e in self._out.get(src, ()))

    def set_edge_metadata(
        self,
        src: str,
        dst: str,
        rel: str,
        weight: int,
        metadata: dict[str, object] | None,
    ) -> None:
        """Attach deterministic metadata to an existing edge if present."""
        if not self._edge_exists(src, dst, rel, weight):
            raise ValueError("cannot annotate unknown edge")
        normalized = self._normalize_edge_metadata(metadata)
        if not normalized:
            return
        edge_cid = Edge(src, dst, rel, weight).cid
        merged = dict(self._edge_metadata.get(edge_cid, {}))
        merged.update(normalized)
        self._edge_metadata[edge_cid] = merged

    def edge_metadata(self, edge: Edge) -> dict[str, object]:
        """Retrieve metadata for ``edge`` (empty dict if absent)."""
        return dict(self._edge_metadata.get(edge.cid, {}))

    # -- weaving -----------------------------------------------------------

    def weave(self, record: dict) -> str:
        """Add a content-addressed record to the Web; returns its CID.

        Weaving is idempotent: the CID is derived from the record's canonical
        bytes, so re-weaving identical content is a no-op and never duplicates.
        """
        node_cid = canonical.cid(record)
        if node_cid not in self.nodes:
            self.nodes[node_cid] = record
            self._out.setdefault(node_cid, [])
            self._in.setdefault(node_cid, [])
        return node_cid

    def link(
        self,
        src: str,
        dst: str,
        rel: str,
        weight: int = 1,
        metadata: dict[str, object] | None = None,
    ) -> Edge:
        """Create a typed edge from ``src`` to ``dst``. Both nodes must exist."""
        if src not in self.nodes:
            raise KeyError(f"unknown source node: {src}")
        if dst not in self.nodes:
            raise KeyError(f"unknown destination node: {dst}")
        edge = Edge(src=src, dst=dst, rel=rel, weight=weight)
        # idempotent on (src, dst, rel, weight)
        if all(e.cid != edge.cid for e in self._out[src]):
            self._out[src].append(edge)
            self._in[dst].append(edge)
        if metadata is not None:
            self.set_edge_metadata(src, dst, rel, weight, metadata)
        return edge

    # -- reading -----------------------------------------------------------

    def get(self, node_cid: str) -> dict | None:
        return self.nodes.get(node_cid)

    def neighbors(self, node_cid: str, rel: str | None = None) -> list[str]:
        """CIDs reachable by one outgoing hop, optionally filtered by relation."""
        edges = self._out.get(node_cid, [])
        if rel is not None:
            edges = [e for e in edges if e.rel == rel]
        return [e.dst for e in edges]

    def outgoing_edges(self, node_cid: str) -> list[Edge]:
        """Return typed outgoing edges for one node, in stored order."""
        return list(self._out.get(node_cid, ()))

    def incoming_edges(self, node_cid: str) -> list[Edge]:
        """Return typed incoming edges for one node, in stored order."""
        return list(self._in.get(node_cid, ()))

    def traverse(
        self,
        start: str,
        depth: int = 2,
        rels: set[str] | None = None,
    ) -> set[str]:
        """Deterministic breadth-first traversal up to ``depth`` hops from ``start``.

        Returns the set of node CIDs reachable (excluding ``start`` itself unless
        it is revisited). Edge relations can be restricted with ``rels``.
        """
        seen: set[str] = set()
        frontier = [start]
        for _ in range(max(0, depth)):
            nxt: list[str] = []
            for node in frontier:
                for edge in sorted(self._out.get(node, []), key=lambda e: (e.rel, e.dst)):
                    if rels is not None and edge.rel not in rels:
                        continue
                    if edge.dst not in seen:
                        seen.add(edge.dst)
                        nxt.append(edge.dst)
            frontier = nxt
            if not frontier:
                break
        return seen

    @property
    def size(self) -> tuple[int, int]:
        """(node_count, edge_count)."""
        edge_count = sum(len(v) for v in self._out.values())
        return len(self.nodes), edge_count
