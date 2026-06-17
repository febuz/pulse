"""
Knot — content unit in the knitweb (2-line post, SHA-256 addressed).

The knot is the atomic content element.  Its 256-bit address is a
content-hash: the same two lines from the same author at the same timestamp
always produce the same address.  Changing any character yields a new knot.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .addressing import addr256, is_valid_addr

KNOT_SCHEMA   = "vpc.knot/1"
MAX_LINE_LEN  = 140
MAX_LINES     = 2


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def canonical_body(line1: str, line2: str, author: str, ts: str) -> str:
    """Deterministic JSON used as hash input.  Identical to the TypeScript version."""
    return json.dumps({"line1": line1, "line2": line2, "author": author, "ts": ts},
                      separators=(",", ":"), ensure_ascii=False)


def compute_knot_addr(line1: str, line2: str, author: str, ts: str) -> str:
    """SHA-256 content address of a knot — the 256-bit knot identifier."""
    return addr256(canonical_body(line1, line2, author, ts))


@dataclass
class Knot:
    """A single content unit in the knitweb."""

    schema: str
    addr: str           # 256-bit content address
    line1: str          # required, trimmed, ≤ MAX_LINE_LEN
    line2: str          # optional (empty string when absent)
    author: str         # fiber DID or 'did:silk:anonymous'
    signature: str      # DID signature over addr (empty = unsigned)
    ts: str             # ISO-8601 UTC

    # Validation state (populated by Pulse engine)
    validation_count: int = 0
    confirmed: bool = False

    @classmethod
    def create(
        cls,
        line1: str,
        line2: str = "",
        author: str = "did:silk:anonymous",
        signature: str = "",
        ts: Optional[str] = None,
    ) -> "Knot":
        ts = ts or _now()
        line1 = line1.strip()
        line2 = line2.strip()
        addr  = compute_knot_addr(line1, line2, author, ts)
        return cls(
            schema=KNOT_SCHEMA,
            addr=addr,
            line1=line1,
            line2=line2,
            author=author,
            signature=signature,
            ts=ts,
        )

    def __repr__(self) -> str:
        return f"Knot(addr={self.addr[:12]}…, line1={self.line1[:30]!r})"


def validate_knot(knot: Knot, max_line_len: int = MAX_LINE_LEN) -> dict:
    """
    Validate a Knot object.  Returns {"ok": True} or {"ok": False, "reason": "..."}.
    """
    if not knot.line1.strip():
        return {"ok": False, "reason": "line1 must not be blank"}
    if len(knot.line1) > max_line_len:
        return {"ok": False, "reason": f"line1 exceeds {max_line_len} chars"}
    if len(knot.line2) > max_line_len:
        return {"ok": False, "reason": f"line2 exceeds {max_line_len} chars"}

    # Verify content address
    expected = compute_knot_addr(knot.line1, knot.line2, knot.author, knot.ts)
    if knot.addr != expected:
        return {"ok": False, "reason": f"addr mismatch: expected {expected}, got {knot.addr}"}

    return {"ok": True}


class KnotRegistry:
    """In-memory registry of knots."""

    def __init__(self, max_knots: int = 10_000) -> None:
        self._knots: Dict[str, Knot] = {}
        self._order: List[str] = []   # insertion order for LRU eviction
        self.max_knots = max_knots

    def add(self, knot: Knot) -> dict:
        v = validate_knot(knot)
        if not v["ok"]:
            return v
        if knot.addr in self._knots:
            return {"ok": True}   # idempotent
        if len(self._knots) >= self.max_knots:
            evict = self._order.pop(0)
            self._knots.pop(evict, None)
        self._knots[knot.addr] = knot
        self._order.append(knot.addr)
        return {"ok": True}

    def get(self, addr: str) -> Optional[Knot]:
        return self._knots.get(addr)

    def list(self, limit: int = 50, offset: int = 0) -> List[Knot]:
        addrs = list(reversed(self._order))  # newest first
        return [self._knots[a] for a in addrs[offset: offset + limit] if a in self._knots]

    def __len__(self) -> int:
        return len(self._knots)
