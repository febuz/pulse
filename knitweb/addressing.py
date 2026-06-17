"""
256-bit addressing — the foundational address space of the knitweb.

Every element (fiber, dot, knot) carries a 256-bit address derived from
SHA-256.  The three independent 256-bit spaces are what bound the maximum
size — and therefore the maximum market cap — of the knitweb economy.
"""

import hashlib
from typing import Union


ADDR_BITS  = 256
ADDR_BYTES = ADDR_BITS // 8   # 32 bytes
ADDR_HEX   = ADDR_BYTES * 2   # 64 hex chars

# Theoretical maximum elements per dimension (fibers, dots, or knots).
MAX_PER_DIM: int = 2 ** ADDR_BITS


def addr256(*parts: Union[str, bytes]) -> str:
    """
    Derive a 256-bit address (64-char hex) from one or more parts.

    Parts are concatenated with a null-byte separator before hashing so
    that addr256("ab", "c") != addr256("a", "bc").
    """
    h = hashlib.sha256()
    for i, part in enumerate(parts):
        if i > 0:
            h.update(b"\x00")
        h.update(part.encode() if isinstance(part, str) else part)
    return h.hexdigest()


def is_valid_addr(addr: str) -> bool:
    """Return True iff *addr* is a well-formed 256-bit hex address."""
    if not isinstance(addr, str) or len(addr) != ADDR_HEX:
        return False
    try:
        int(addr, 16)
        return True
    except ValueError:
        return False


def addr_distance(a: str, b: str) -> int:
    """XOR distance between two 256-bit addresses (Kademlia metric)."""
    return int(a, 16) ^ int(b, 16)
