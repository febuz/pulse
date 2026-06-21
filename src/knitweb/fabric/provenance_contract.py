"""Stable provenance query contract for external (Lens) consumers.

A *Lens* reads provenance out of the woven Web from outside Pulse and needs a
boundary it can rely on: fixed inputs, fixed output shape, deterministic order, and
no silent data loss. This module is that boundary. It composes the existing
:mod:`knitweb.fabric.provenance` walk (full-depth, relation-filtered ancestry and
origins) into one frozen, read-only result so a Lens never re-implements the graph
logic or depends on incidental dict/iteration order.

The one guarantee the raw walk leaves implicit and this contract makes explicit is
**dangling-reference visibility**. The Web links edges between content-addressed CIDs,
but an antecedent CID a record derives from may not (yet) have its node record present:
a peer-fed edge whose target node hasn't synced, or a record dropped after the edge was
woven. :func:`provenance_query` resolves every reachable ancestor against the Web and
partitions them: ancestors whose record is present go in ``present``; ancestors whose
``web.get`` returns ``None`` go in ``missing`` — a distinct, visible list, never silently
dropped. ``origins`` (the raw-material leaves) are reported the same partitioned way.

Two properties make it safe as a stable boundary:

  * **Deterministic** — every list in the result is sorted by CID, so repeated calls
    over identical Web content return equal results and the order is identical across
    different node/edge insertion orders. No wall-clock, randomness, or iteration-order
    leaks in.
  * **Read-only** — building a result only reads the Web (the underlying ancestry walk
    and ``web.get``); it never weaves, links, or rewrites any record or edge.
"""

from __future__ import annotations

from dataclasses import dataclass

from .provenance import ancestry, origins
from .web import Web

__all__ = ["ProvenanceQueryResult", "provenance_query"]


@dataclass(frozen=True)
class ProvenanceQueryResult:
    """The stable, read-only result of a Lens provenance query.

    Fields (every CID list is sorted, so the shape is byte-stable across calls and
    insertion orders):

      * ``root`` — the CID the query started from (excluded from the ancestry).
      * ``rels`` — the sorted relation-filter names applied, or ``None`` for "all edges".
      * ``present`` — ancestor CIDs whose node record is present in the Web.
      * ``missing`` — ancestor CIDs reachable via an edge but **not** present in the Web
        (``web.get`` is ``None``): dangling references, surfaced rather than dropped.
      * ``origin_present`` — raw-material leaf ancestors (no further antecedents) that
        are present in the Web.
      * ``origin_missing`` — leaf ancestors that are dangling references.
    """

    root: str
    rels: tuple[str, ...] | None
    present: tuple[str, ...]
    missing: tuple[str, ...]
    origin_present: tuple[str, ...]
    origin_missing: tuple[str, ...]

    @property
    def has_dangling(self) -> bool:
        """True iff any reachable ancestor is a dangling (missing-node) reference."""
        return bool(self.missing)


def provenance_query(
    web: Web,
    start: str,
    rels: "set[str] | None" = None,
) -> ProvenanceQueryResult:
    """Run the stable Lens provenance query for ``start`` over ``web``.

    Walks the full-depth, relation-filtered ancestry of ``start`` (see
    :func:`knitweb.fabric.provenance.ancestry`), then resolves every reachable
    ancestor against the Web and partitions ancestors and origins into present vs.
    missing (dangling) references. Pass ``rels`` to restrict to provenance edge types
    (e.g. ``{"derived-from"}``); ``None`` follows every edge. Read-only and
    deterministic — see the module docstring for the ordering and isolation guarantees.
    """
    ancestors = ancestry(web, start, rels)
    leaves = set(origins(web, start, rels))

    present: list[str] = []
    missing: list[str] = []
    origin_present: list[str] = []
    origin_missing: list[str] = []
    for cid in ancestors:
        is_present = web.get(cid) is not None
        (present if is_present else missing).append(cid)
        if cid in leaves:
            (origin_present if is_present else origin_missing).append(cid)

    return ProvenanceQueryResult(
        root=start,
        rels=tuple(sorted(rels)) if rels is not None else None,
        present=tuple(sorted(present)),
        missing=tuple(sorted(missing)),
        origin_present=tuple(sorted(origin_present)),
        origin_missing=tuple(sorted(origin_missing)),
    )
