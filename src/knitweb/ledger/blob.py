"""Blob — account balance state (one of the seven core primitives).

A Blob is the mutable balance state of an account: a map of token symbol to an
**integer** amount (FBR-wei), plus a monotonic nonce that prevents replay. All
balance arithmetic is integer and total-preserving; the Blob never holds floats.

Blobs are plain helpers here — the canonical, content-addressed, chained
commitment to a Blob's state lives in :mod:`knitweb.ledger.fiber`.
"""

from __future__ import annotations

__all__ = ["balance_of", "credit", "debit", "normalize"]


def normalize(balances: dict[str, int]) -> dict[str, int]:
    """Return a copy with zero balances dropped and integer values enforced."""
    out: dict[str, int] = {}
    for sym, amt in balances.items():
        if not isinstance(amt, int) or isinstance(amt, bool):
            raise TypeError(f"balance for {sym} must be int, got {type(amt).__name__}")
        if amt < 0:
            raise ValueError(f"balance for {sym} is negative: {amt}")
        if amt > 0:
            out[sym] = amt
    return out


def balance_of(balances: dict[str, int], symbol: str) -> int:
    """Return the integer balance for ``symbol`` (0 if absent)."""
    return int(balances.get(symbol, 0))


def credit(balances: dict[str, int], symbol: str, amount: int) -> dict[str, int]:
    """Return a new balances map with ``amount`` added to ``symbol``."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError("amount must be int")
    if amount < 0:
        raise ValueError("credit amount must be non-negative")
    out = dict(balances)
    out[symbol] = balance_of(out, symbol) + amount
    return normalize(out)


def debit(balances: dict[str, int], symbol: str, amount: int) -> dict[str, int]:
    """Return a new balances map with ``amount`` removed; raises on overdraft."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError("amount must be int")
    if amount < 0:
        raise ValueError("debit amount must be non-negative")
    current = balance_of(balances, symbol)
    if amount > current:
        raise ValueError(f"overdraft: balance {current} < debit {amount} of {symbol}")
    out = dict(balances)
    out[symbol] = current - amount
    return normalize(out)
