"""Provenance queries over the Web — reconstruct a thing's origin + processing chain.

The fabric records *what happened* (a chemistry reaction, a supply-chain process, a
finance settlement) as content-addressed nodes, and links a derived record to the
records it came from with typed Web edges (e.g. ``derived-from`` / ``consumes`` /
``settles``). This module answers the knowledge-graph question those links exist for:

    "Given this product/record, what is its full provenance — every raw-material
     origin and processing step it derives from?"

:func:`ancestry` walks those edges to **full depth** (``Web.traverse`` caps at a fixed
hop count; provenance must follow the chain all the way to the roots), deterministically.
:func:`provenance` returns the closure with the records attached, and
:func:`origins` isolates the leaf nodes (raw-material origins — records with no further
antecedents). :func:`is_acyclic` guards the invariant that provenance is a DAG (nothing
derives from itself). Pure graph logic over the merged ``fabric.web.Web`` — no new deps.
"""

from __future__ import annotations

from .web import Web

__all__ = ["ancestry", "provenance", "origins", "is_acyclic"]


def _antecedents(web: Web, node: str, rels: "set[str] | None") -> list[str]:
    """Direct records ``node`` derives from (one hop), deterministically ordered."""
    if rels is None:
        return sorted(web.neighbors(node))
    out: set[str] = set()
    for rel in rels:
        out.update(web.neighbors(node, rel))
    return sorted(out)


def ancestry(web: Web, start: str, rels: "set[str] | None" = None) -> list[str]:
    """All records ``start`` derives from, via ``rels`` edges, to full depth.

    Deterministic breadth-first order, ``start`` excluded, each ancestor once. Safe on
    cyclic graphs (visited-set bounded). Pass ``rels`` to restrict to provenance edge
    types (e.g. ``{"derived-from"}``); ``None`` follows every edge.
    """
    seen: set[str] = set()
    order: list[str] = []
    frontier = [start]
    while frontier:
        nxt: list[str] = []
        for node in frontier:
            for dst in _antecedents(web, node, rels):
                if dst not in seen and dst != start:
                    seen.add(dst)
                    order.append(dst)
                    nxt.append(dst)
        frontier = nxt
    return order


def provenance(web: Web, start: str, rels: "set[str] | None" = None) -> dict:
    """The provenance closure of ``start``: its ancestors + their records.

    Returns ``{"root", "ancestors": [cid...], "records": {cid: record}}``. Missing
    nodes (a cited CID the Web hasn't seen) map to ``None`` so dangling references are
    visible rather than silently dropped.
    """
    cids = ancestry(web, start, rels)
    return {
        "root": start,
        "ancestors": cids,
        "records": {c: web.get(c) for c in cids},
    }


def origins(web: Web, start: str, rels: "set[str] | None" = None) -> list[str]:
    """The leaf ancestors of ``start`` — raw-material origins with no antecedents."""
    return [c for c in ancestry(web, start, rels) if not _antecedents(web, c, rels)]


def is_acyclic(web: Web, start: str, rels: "set[str] | None" = None) -> bool:
    """True iff the provenance reachable from ``start`` contains no cycle (a DAG).

    Provenance must be acyclic: nothing can derive (transitively) from itself.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {}

    def visit(node: str) -> bool:
        color[node] = GREY
        for dst in _antecedents(web, node, rels):
            c = color.get(dst, WHITE)
            if c == GREY:
                return False                 # back-edge -> cycle
            if c == WHITE and not visit(dst):
                return False
        color[node] = BLACK
        return True

    return visit(start)
