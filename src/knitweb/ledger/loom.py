"""Loom — the validation protocol (one of the seven core primitives).

The Loom is the *only* determinism-critical surface in Knitweb. It validates Knits
and the state transitions they cause, enforcing the network's economic invariants:

  * well-formedness (positive integer amount, sender ≠ receiver),
  * dual signatures valid over the canonical record (both parties agreed),
  * nonce match (replay/double-spend prevention),
  * no overdraft (a node cannot spend what it does not hold),
  * conservation of value (a transfer's debit equals its credit, exactly).

Every check is integer/boolean; the heavy world (GPU compute, scoring) lives in
other layers and only ever hands the Loom hashes and verdicts.
"""

from __future__ import annotations

from . import blob
from .fiber import Fiber
from .knit import MAINNET, Knit

__all__ = [
    "validate_knit",
    "apply_to_sender",
    "apply_to_receiver",
    "conserves_value",
    "LoomError",
]


class LoomError(ValueError):
    """Raised when a state transition violates a Loom invariant."""


def validate_knit(knit: Knit, expected_network: int = MAINNET) -> tuple[bool, str]:
    """Validate a fully-signed Knit. Returns (ok, reason). ``ok`` implies reason ''.

    ``expected_network`` is the validating web's own network id; a Knit bound to a
    different network is refused, so a signed transfer from one PLS web can never
    be replayed on another (EIP-155-style anti-replay). The network id is inside
    the signed bytes, so it cannot be altered without invalidating the signatures.
    """
    from ..core import crypto  # local import keeps the core import graph acyclic

    if not isinstance(knit.amount, int) or isinstance(knit.amount, bool):
        return False, "amount must be int"
    if knit.amount <= 0:
        return False, "amount must be positive"
    if knit.network != expected_network:
        return False, f"wrong network: knit {knit.network} != expected {expected_network}"
    if knit.from_pub == knit.to_pub:
        return False, "sender and receiver must differ"
    if knit.from_nonce < 0:
        return False, "from_nonce must be non-negative"
    if not knit.from_sig or not knit.to_sig:
        return False, "knit must carry both signatures"
    if not crypto.verify(knit.from_pub, knit.signing_bytes, knit.from_sig):
        return False, "invalid sender signature"
    if not crypto.verify(knit.to_pub, knit.signing_bytes, knit.to_sig):
        return False, "invalid receiver signature"
    return True, ""


def apply_to_sender(prev: Fiber, knit: Knit, expected_network: int = MAINNET) -> Fiber:
    """Produce the sender's next Fiber after sending ``knit``. Raises on violation."""
    if knit.from_pub != prev.owner:
        raise LoomError("knit sender does not match fiber owner")
    ok, reason = validate_knit(knit, expected_network)
    if not ok:
        raise LoomError(f"invalid knit: {reason}")
    if knit.from_nonce != prev.nonce:
        raise LoomError(
            f"nonce mismatch: knit {knit.from_nonce} != account {prev.nonce}"
        )
    new_balances = blob.debit(prev.balances, knit.symbol, knit.amount)  # raises on overdraft
    return Fiber(
        owner=prev.owner,
        seq=prev.seq + 1,
        balances=new_balances,
        nonce=prev.nonce + 1,        # outgoing transfer consumes the nonce
        prev=prev.cid,
        knit=knit.id,
    )


def apply_to_receiver(prev: Fiber, knit: Knit, expected_network: int = MAINNET) -> Fiber:
    """Produce the receiver's next Fiber after receiving ``knit``. Raises on violation."""
    if knit.to_pub != prev.owner:
        raise LoomError("knit receiver does not match fiber owner")
    ok, reason = validate_knit(knit, expected_network)
    if not ok:
        raise LoomError(f"invalid knit: {reason}")
    new_balances = blob.credit(prev.balances, knit.symbol, knit.amount)
    return Fiber(
        owner=prev.owner,
        seq=prev.seq + 1,
        balances=new_balances,
        nonce=prev.nonce,            # receiving does not consume the receiver's nonce
        prev=prev.cid,
        knit=knit.id,
    )


def conserves_value(
    sender_before: Fiber,
    sender_after: Fiber,
    receiver_before: Fiber,
    receiver_after: Fiber,
    symbol: str,
) -> bool:
    """True iff the two-sided transition conserves ``symbol`` exactly."""
    before = sender_before.balance(symbol) + receiver_before.balance(symbol)
    after = sender_after.balance(symbol) + receiver_after.balance(symbol)
    return before == after
