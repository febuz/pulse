"""Metrics — dependency-free, integer-only observability for a live web node.

A running web node converges silently: records weave, frames cross the wire,
peers get banned — but without a window into those events an operator is blind to
why a node *fails* to converge (a peer that refuses every frame, a flood of
oversized junk, a sync that pulls nothing). This is that window: the minimal
counter/gauge surface every production P2P stack carries (Bitcoin Core's
``g_stats`` / libp2p's metrics, Prometheus-style), but kept to the web's
non-negotiables — **integers only, no float, no wall-clock, deterministic**.

A :class:`Metrics` instance is a flat bag of named integer series. Two flavours,
distinguished only by intent:

  * **counters** — monotonic, only ever incremented (records woven, frames in).
    A counter never decreases; :meth:`incr` rejects a negative delta.
  * **gauges** — a value that can move either way (e.g. a current depth). Set
    with :meth:`gauge`.

The point is :meth:`snapshot`: a plain ``dict[str, int]`` with **sorted keys**
and integer values, so it is directly canonical-CBOR-encodable for export to a
peer and byte-identical across two nodes that observed the same event stream. It
touches no signed record and no hash path — a fresh Knit's CID is unchanged
whether or not a node is being metered.

This module is a reusable primitive: it deliberately knows nothing about the
fabric or the node stacks, so either node layer can adopt it independently.
"""

from __future__ import annotations

from typing import Dict, List

__all__ = ["Metrics", "FABRIC_METRICS"]

# Canonical metric names the FabricNode emits. Listed here (not buried in the
# node) so a dashboard/exporter can enumerate the expected series, and so two
# nodes agree on the vocabulary of what is being measured.
FABRIC_METRICS: List[str] = [
    "records_woven",        # records woven into the local Web (local + ingested)
    "broadcasts_sent",      # per-peer broadcast frames that were acked
    "broadcasts_failed",    # per-peer broadcast frames that errored (offline peer)
    "sync_pulls",           # records newly woven via a catch-up sync_from pull
    "frames_in",            # decoded request frames accepted by the dispatch path
    "frames_out",           # response frames the node produced
    "frames_malformed",     # undecodable / non-canonical inbound frames
    "frames_oversized",     # inbound frames over the wire size cap
    "banned_refusals",      # requests refused because the peer is banned
]


def _require_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise TypeError("metric name must be a non-empty str")


def _require_int(label: str, value: int, *, minimum: int) -> int:
    # bool is an int subclass; a flag is never a metric value.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{label} must be int, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{label} must be >= {minimum} (got {value})")
    return value


class Metrics:
    """A flat, integer-only registry of named counters and gauges.

    All values are plain Python ``int`` (unbounded big-ints, so a counter never
    silently wraps). A name never seen reads as ``0``. The registry is purely
    node-local bookkeeping: it carries no wall-clock and no randomness, so a
    :meth:`snapshot` is a deterministic function of the event stream alone.
    """

    def __init__(self) -> None:
        self._values: Dict[str, int] = {}

    # ── Mutations ────────────────────────────────────────────────────────────

    def incr(self, name: str, delta: int = 1) -> int:
        """Add ``delta`` (>= 0) to monotonic counter ``name``; returns the new total.

        A counter is monotonic by construction — a negative delta is rejected so a
        counter can never run backwards.
        """
        _require_name(name)
        _require_int("delta", delta, minimum=0)
        total = self._values.get(name, 0) + delta
        self._values[name] = total
        return total

    def gauge(self, name: str, value: int) -> int:
        """Set gauge ``name`` to ``value`` (>= 0); returns it.

        Unlike a counter, a gauge may move down as well as up (a current depth),
        but it stays a non-negative integer.
        """
        _require_name(name)
        self._values[name] = _require_int("value", value, minimum=0)
        return value

    # ── Queries ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> int:
        """The current value of ``name`` (0 if never touched)."""
        _require_name(name)
        return self._values.get(name, 0)

    def snapshot(self) -> Dict[str, int]:
        """A deterministic, canonical-CBOR-safe ``{name: int}`` of every series.

        Keys are returned in sorted order so the dict (and its canonical-CBOR
        encoding) is byte-identical across two nodes that observed the same event
        stream. The returned map is a fresh copy — mutating it cannot corrupt the
        live registry.
        """
        return {name: self._values[name] for name in sorted(self._values)}

    def tracked(self) -> int:
        """How many distinct metric series have ever been touched."""
        return len(self._values)
