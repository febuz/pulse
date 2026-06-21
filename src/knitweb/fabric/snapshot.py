"""Deterministic, read-only Web snapshot for external (Lens) consumers.

A *Lens* reads the woven Web from outside Pulse and must never reach back into
fabric state. :func:`web_snapshot` returns a self-contained, deeply-copied view of
the full Web — its state root, node/edge counts, the node records keyed by CID, and
the deterministic JSON-LD export — built by composing the existing content-derived
primitives (:func:`~knitweb.fabric.items.web_state_root` and
:func:`~knitweb.fabric.jsonld.export_web`).

Two properties make it safe as a stable boundary:

  * **Deterministic** — repeated calls over identical Web content return equal,
    byte-stable snapshots (nodes sorted by CID, edges in canonical order), so a Lens
    can cache and diff snapshots and the committed ``state_root`` never changes for
    unchanged content. No wall-clock, randomness, or insertion-order leaks in.
  * **Non-mutating & isolated** — building a snapshot never weaves, links, or rewrites
    any record, signature, or feed, and the returned structure is a deep copy, so a
    consumer mutating it cannot corrupt live fabric state.
"""

from __future__ import annotations

import copy

from .items import web_state_root
from .jsonld import export_web
from .web import Web

__all__ = ["web_snapshot"]


def web_snapshot(web: Web) -> dict:
    """Return a deterministic, read-only snapshot of *web*.

    The snapshot is a plain dict with five keys: ``state_root`` (the full
    nodes-and-edges commitment), ``node_count`` / ``edge_count``, ``records`` (the
    node records keyed by CID, CID-sorted), and ``jsonld`` (the deterministic
    JSON-LD/DKG export). See the module docstring for the determinism and isolation
    guarantees.
    """
    node_count, edge_count = web.size
    snapshot = {
        "state_root": web_state_root(web),
        "node_count": node_count,
        "edge_count": edge_count,
        "records": {cid: web.nodes[cid] for cid in sorted(web.nodes)},
        "jsonld": export_web(web),
    }
    # Deep copy so a Lens mutating the snapshot can never reach back into the live
    # Web's records or edge objects — the boundary is read-only by construction.
    return copy.deepcopy(snapshot)
