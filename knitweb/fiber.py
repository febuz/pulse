"""
Fiber — node in the knitweb graph (graph-theory: vertex / node).

A fiber is a spider (participant) in the knitweb.  Its 256-bit address is
derived from the participant's DID.  Fibers with an FBR balance > 0 are
voting-eligible; silk fibers start non-voting.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional

from .addressing import addr256, is_valid_addr

FIBER_SCHEMA = "vpc.fiber/1"


@dataclass
class Fiber:
    """A single node in the knitweb graph."""

    did: str                     # Decentralised Identifier of the spider
    addr: str                    # SHA-256(did) — 256-bit fiber address
    label: str = ""              # human-readable alias (optional, ≤ 64 chars)
    silk: bool = True            # True = free tier; False = paid VPC node
    created_at: str = field(default_factory=lambda: _now())
    last_seen_at: str = field(default_factory=lambda: _now())

    # Derived / mutable state (not part of the address)
    knot_count: int = 0          # knots (posts) contributed
    validation_count: int = 0    # knots validated (edges cast)

    @classmethod
    def from_did(cls, did: str, label: str = "", silk: bool = True) -> "Fiber":
        return cls(
            did=did,
            addr=addr256(did),
            label=label[:64],
            silk=silk,
        )

    def touch(self) -> None:
        self.last_seen_at = _now()

    def __repr__(self) -> str:
        return f"Fiber(addr={self.addr[:12]}…, did={self.did[:24]}…)"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class FiberRegistry:
    """In-memory registry of known fibers (nodes)."""

    def __init__(self) -> None:
        self._fibers: Dict[str, Fiber] = {}   # addr → Fiber

    def register(self, did: str, label: str = "", silk: bool = True) -> Fiber:
        """Register or touch an existing fiber."""
        addr = addr256(did)
        if addr in self._fibers:
            f = self._fibers[addr]
            f.touch()
            if label:
                f.label = label[:64]
            return f
        f = Fiber.from_did(did, label=label, silk=silk)
        self._fibers[addr] = f
        return f

    def get(self, addr: str) -> Optional[Fiber]:
        return self._fibers.get(addr)

    def get_by_did(self, did: str) -> Optional[Fiber]:
        return self._fibers.get(addr256(did))

    def all(self) -> list[Fiber]:
        return list(self._fibers.values())

    def __len__(self) -> int:
        return len(self._fibers)
