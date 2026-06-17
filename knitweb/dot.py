"""
Dot — edge in the knitweb graph (graph-theory: edge / arc).

A dot connects two fibers or a fiber to a knot.  Its 256-bit address is
derived from the addresses of both endpoints so the same logical connection
always maps to the same dot address, regardless of direction (undirected).

Edge types:
  GOSSIP    — fiber gossips a knot to another fiber
  VALIDATES — fiber casts a validation vote on a knot
  ANCHORS   — fiber anchors a knot to a chain tx
  BRIDGES   — fiber bridges two network layers (silk ↔ VPC mainnet)
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

from .addressing import addr256, is_valid_addr

DOT_SCHEMA = "vpc.dot/1"


class DotType(str, Enum):
    GOSSIP    = "gossip"
    VALIDATES = "validates"
    ANCHORS   = "anchors"
    BRIDGES   = "bridges"


@dataclass
class Dot:
    """A single edge in the knitweb graph."""

    addr: str           # SHA-256(sorted(src, dst) + type) — 256-bit dot address
    src: str            # source fiber/knot addr (256-bit)
    dst: str            # destination fiber/knot addr (256-bit)
    dot_type: DotType
    created_at: str = field(default_factory=lambda: _now())
    weight: float = 1.0  # connection strength (increases with repeated interaction)

    @classmethod
    def create(cls, src: str, dst: str, dot_type: DotType, weight: float = 1.0) -> "Dot":
        # Canonical address: sort endpoints so the edge is undirected
        lo, hi = sorted([src, dst])
        addr = addr256(lo, hi, dot_type.value)
        return cls(addr=addr, src=src, dst=dst, dot_type=dot_type, weight=weight)

    def __repr__(self) -> str:
        return f"Dot(addr={self.addr[:12]}…, {self.src[:8]}→{self.dst[:8]}, type={self.dot_type})"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class DotRegistry:
    """In-memory registry of edges (dots) in the knitweb."""

    def __init__(self) -> None:
        self._dots: Dict[str, Dot] = {}   # dot addr → Dot
        # Index by endpoint for adjacency queries
        self._by_src: Dict[str, list[str]] = {}   # src addr → [dot addrs]
        self._by_dst: Dict[str, list[str]] = {}

    def add(self, dot: Dot) -> Dot:
        """Idempotent — re-adding an existing dot just increments weight."""
        if dot.addr in self._dots:
            self._dots[dot.addr].weight += dot.weight
            return self._dots[dot.addr]
        self._dots[dot.addr] = dot
        self._by_src.setdefault(dot.src, []).append(dot.addr)
        self._by_dst.setdefault(dot.dst, []).append(dot.addr)
        return dot

    def connect(
        self,
        src: str,
        dst: str,
        dot_type: DotType,
        weight: float = 1.0,
    ) -> Dot:
        """Create and register a dot between two addresses."""
        return self.add(Dot.create(src, dst, dot_type, weight))

    def get(self, addr: str) -> Optional[Dot]:
        return self._dots.get(addr)

    def neighbours(self, addr: str) -> list[Dot]:
        """Return all dots touching *addr* (as src or dst)."""
        seen: set[str] = set()
        out: list[Dot] = []
        for da in self._by_src.get(addr, []) + self._by_dst.get(addr, []):
            if da not in seen:
                seen.add(da)
                if d := self._dots.get(da):
                    out.append(d)
        return out

    def all(self) -> list[Dot]:
        return list(self._dots.values())

    def __len__(self) -> int:
        return len(self._dots)
