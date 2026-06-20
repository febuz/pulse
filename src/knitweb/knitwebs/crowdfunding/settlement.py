"""Execute a certified crowdfunding settlement as real ledger transfers.

A ``crowdfunding-settlement`` is a deterministic *instruction* (release to the beneficiary, or
refund each pledger). This module turns that instruction into actual PLS movement on the L1
ledger: for each payout entry it completes a two-party Knit transfer from the campaign **escrow**
account to the payee, reusing :meth:`ledger.node.AccountNode.transfer_to`.

Safety: the settlement is **audited first** (validly authority-signed *and* recomputes from the
pledges) before any value moves, and conservation is enforced by the ledger itself (dual-signed
Knits, no overdraft). This executes *local* transfers (both accounts in-process) — the provable
economic core; the distributed escrow-proposes / payee-accepts handshake over P2P is L2 wiring
on top.

Dependency note: a crowdfunding plugin importing L1 ``ledger`` is the one explicitly-justified
cross-layer dependency here — settlement *execution* is inherently a ledger operation.
"""

from __future__ import annotations

from typing import Dict, List

from ...ledger.knit import Knit
from ...ledger.node import AccountNode
from .campaign import audit_settlement, settlement_entries

__all__ = ["EscrowError", "execute_settlement"]


class EscrowError(Exception):
    """The escrow cannot satisfy the settlement (underfunded, or an unknown/mismatched payee)."""


def execute_settlement(
    settlement_att,
    outcome_record: dict,
    campaign_record: dict,
    pledges: List[dict],
    escrow: AccountNode,
    payees: Dict[str, AccountNode],
    *,
    timestamp: int,
    symbol: str = "PLS",
    applied: set | None = None,
) -> List[Knit]:
    """Move PLS escrow→payees per a certified settlement; return the applied Knits.

    ``payees`` maps a payout address (the beneficiary, or each pledger) to its ``AccountNode``.
    ``applied`` is an optional, caller-persisted set of already-executed settlement CIDs: pass it
    to make execution **one-shot/idempotent** — re-executing the same settlement raises rather
    than paying again (the escrow-balance check alone does NOT stop a replay against an over-
    funded or re-topped escrow, since each transfer uses a fresh nonce).

    Raises :class:`ValueError` if the settlement does not audit, or :class:`EscrowError` if it was
    already applied, a payee account is missing/mismatched, a payee would be a self-transfer or on
    a different network, or the escrow is underfunded.
    """
    if not audit_settlement(settlement_att, outcome_record, campaign_record, pledges):
        raise ValueError("settlement does not audit; refusing to move value")

    settlement_cid = settlement_att.cid
    if applied is not None and settlement_cid in applied:
        raise EscrowError(f"settlement {settlement_cid} already executed")

    _mode, entries = settlement_entries(outcome_record, campaign_record, pledges)
    total = sum(amount for _cid, _payee, amount in entries)

    # Validate EVERYTHING before moving any value, so a per-transfer ledger rule can never fail
    # mid-loop and leave a partial payout (the all-or-nothing guarantee). The checks below cover
    # exactly the per-transfer invariants AccountNode.transfer_to / validate_knit would enforce:
    # a known matching payee account, sender != receiver, same network, and enough total funds.
    for payee_addr in {payee for _cid, payee, _amount in entries}:
        node = payees.get(payee_addr)
        if node is None:
            raise EscrowError(f"no account provided for payee {payee_addr}")
        if node.address != payee_addr:
            raise EscrowError(f"payee node address mismatch for {payee_addr}")
        if payee_addr == escrow.address:
            raise EscrowError("a payee may not be the escrow itself (self-transfer)")
        if node.network != escrow.network:
            raise EscrowError(f"payee {payee_addr} is on a different network than the escrow")
    if escrow.balance(symbol) < total:
        raise EscrowError(f"escrow has {escrow.balance(symbol)} {symbol}, settlement needs {total}")

    knits: List[Knit] = []
    for index, (_cid, payee_addr, amount) in enumerate(entries):
        knit = escrow.transfer_to(payees[payee_addr], symbol, amount, timestamp + index)
        knits.append(knit)
    if applied is not None:
        applied.add(settlement_cid)
    return knits
