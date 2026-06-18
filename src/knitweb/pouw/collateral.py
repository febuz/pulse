"""Collateral sizing — make fraud never net-positive (the slashing economics).

``pouw/dispute.py`` lets a detected mismatch *slash* a worker's staked collateral, and
guarantees (via ``release_delay > dispute_window``) that a paid worker can't withdraw before
the slash could land. But slashing only deters fraud if the **stake is large enough**: if a
worker can collect more in escrow than it can lose in collateral, cheating is still
+EV. This module is the missing sizing rule (``docs/PROOF_OF_USEFUL_WORK.md`` §4.4, backlog
B6; EigenLayer's "collateral must cover the value at risk").

The value at risk is *cumulative*: while a worker's dispute windows are open it may have
several submissions pending at once, and a single fraud-and-flee could try to collect **all**
of their escrows before any slash lands. So the worker's stake must cover the **sum of
escrows pending within the window**, not just one job:

    payout_at_risk = Σ escrow(pending submissions)
    required_collateral = ⌈ payout_at_risk · margin ⌉          (margin ≥ 1)
    sufficiently_collateralized  ⇔  collateral ≥ required_collateral

With ``collateral ≥ payout_at_risk`` (margin 1:1, the minimum), a detected fraud loses at
least as much stake as it could ever gain in escrow — fraud is net ≤ 0, so honest work
dominates. A ``margin`` above 1 adds a safety buffer (e.g. to cover gas/price drift).

Everything is integer PLS-wei; the margin is an exact integer ratio so no float touches the
path. Pure policy — it imports nothing from the job/escrow/quorum layers; a caller checks
:func:`is_sufficiently_collateralized` *before* accepting a submission into
``pouw/dispute.py`` (or uses :func:`max_backed_payout` to cap how much new escrow a given
stake may back).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

__all__ = [
    "Margin",
    "payout_at_risk",
    "required_collateral",
    "is_sufficiently_collateralized",
    "max_backed_payout",
    "fraud_is_profitable",
]


def _require_int(name: str, value: int, *, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value})")


@dataclass(frozen=True)
class Margin:
    """An exact integer safety ratio ``num/den`` (≥ 1) applied to the payout-at-risk.

    ``Margin(1, 1)`` is the minimum sound buffer (stake ≥ value at risk). ``Margin(3, 2)``
    requires 1.5× over-collateralization. Kept as a rational so no float enters the
    PLS-wei path; collateral amounts are rounded **up** (a short stake is never "enough").
    """

    num: int = 1
    den: int = 1

    def __post_init__(self) -> None:
        _require_int("margin.num", self.num, minimum=1)
        _require_int("margin.den", self.den, minimum=1)
        if self.num < self.den:
            raise ValueError(
                f"margin must be >= 1 (got {self.num}/{self.den}); under-collateralization "
                "would make fraud profitable"
            )

    def apply_ceil(self, amount: int) -> int:
        """``⌈ amount · num / den ⌉`` in exact integer arithmetic."""
        _require_int("amount", amount, minimum=0)
        return (amount * self.num + self.den - 1) // self.den


_UNIT = Margin(1, 1)


def payout_at_risk(pending_escrows: Iterable[int]) -> int:
    """Total escrow a worker could collect-then-flee within its open dispute windows.

    The sum of every pending submission's escrow — the worst case a single fraud could
    try to walk away with before slashing lands.
    """
    escrows: List[int] = list(pending_escrows)
    total = 0
    for i, e in enumerate(escrows):
        _require_int(f"pending_escrows[{i}]", e, minimum=0)
        total += e
    return total


def required_collateral(payout: int, margin: Margin = _UNIT) -> int:
    """Minimum stake to back ``payout`` PLS-wei of at-risk escrow at ``margin`` (≥ value)."""
    _require_int("payout", payout, minimum=0)
    if not isinstance(margin, Margin):
        raise TypeError("margin must be a Margin")
    return margin.apply_ceil(payout)


def is_sufficiently_collateralized(
    collateral: int, payout: int, margin: Margin = _UNIT
) -> bool:
    """True iff ``collateral`` covers the margined ``payout`` at risk (fraud is non-profitable)."""
    _require_int("collateral", collateral, minimum=0)
    return collateral >= required_collateral(payout, margin)


def max_backed_payout(collateral: int, margin: Margin = _UNIT) -> int:
    """The largest payout-at-risk a given ``collateral`` may safely back at ``margin``.

    The inverse of :func:`required_collateral`: ``⌊ collateral · den / num ⌋`` — rounded
    **down** so the returned bound is always actually covered by the stake.
    """
    _require_int("collateral", collateral, minimum=0)
    if not isinstance(margin, Margin):
        raise TypeError("margin must be a Margin")
    return (collateral * margin.den) // margin.num


def fraud_is_profitable(collateral: int, payout: int, margin: Margin = _UNIT) -> bool:
    """True iff a detected fraud could still net the worker a gain (stake < value at risk).

    The exact negation of :func:`is_sufficiently_collateralized`; named for callers that
    want to assert the *bad* condition is impossible.
    """
    return not is_sufficiently_collateralized(collateral, payout, margin)
