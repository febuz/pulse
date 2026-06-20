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

from ...core import crypto
from ...ledger import knitweb as _kw
from ...ledger.knit import Knit
from ...ledger.node import AccountNode
from .campaign import audit_settlement, settlement_entries

__all__ = ["EscrowError", "execute_settlement", "validate_payout", "SettlementSession"]


class EscrowError(Exception):
    """The escrow cannot satisfy the settlement (underfunded, or an unknown/mismatched payee)."""


def validate_payout(
    proposed_knit: Knit,
    settlement_att,
    outcome_record: dict,
    campaign_record: dict,
    pledges: List[dict],
    payee_pub: str,
    *,
    symbol: str = "PLS",
) -> bool:
    """A payee's independent check before co-signing an escrow→payee Knit in a *distributed*
    settlement (the security primitive for both the escrow-push and payee-claim models).

    Returns ``True`` iff: the settlement audits (validly authority-signed AND recomputes from the
    pledges); the proposed Knit is addressed to **this** payee, in the right ``symbol``, for a
    positive amount the settlement actually owes this payee. So a malicious or buggy escrow cannot
    get a payee to co-sign a transfer the settlement does not entitle them to. Returns ``False``
    (never raises) on any malformed input. (Per-entry consumption — not accepting more transfers
    than entries — is the settlement session's job, layered on top.)
    """
    try:
        if not audit_settlement(settlement_att, outcome_record, campaign_record, pledges):
            return False
        if getattr(proposed_knit, "to_pub", None) != payee_pub:
            return False
        if getattr(proposed_knit, "symbol", None) != symbol:
            return False
        amount = getattr(proposed_knit, "amount", None)
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            return False
        payee_addr = crypto.address(payee_pub)
        _mode, entries = settlement_entries(outcome_record, campaign_record, pledges)
        owed = [amt for _cid, payee, amt in entries if payee == payee_addr]
        return amount in owed
    except (ValueError, TypeError, KeyError):
        return False


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


class SettlementSession:
    """Resumable, payee-validated escrow-push settlement — distributed Phase 1 (in-process).

    Drives a certified settlement entry-by-entry: the escrow proposes a sender-signed Knit, the
    payee independently authorises it (:func:`validate_payout`) and accepts, then the escrow
    applies it and advances a resumable cursor. It survives interruption (resume from the cursor)
    and is idempotent across sessions when given an ``applied`` set of settlement CIDs. Real
    cross-node transport is the Phase-2 wiring; this is the provable in-process core that puts the
    propose → payee-validate → accept → apply handshake under test.
    """

    def __init__(self, settlement_att, outcome_record, campaign_record, pledges,
                 escrow: AccountNode, payees: Dict[str, AccountNode], *,
                 symbol: str = "PLS", applied: set | None = None):
        if not audit_settlement(settlement_att, outcome_record, campaign_record, pledges):
            raise ValueError("settlement does not audit; refusing to settle")
        self._att = settlement_att
        self._outcome = outcome_record
        self._campaign = campaign_record
        self._pledges = pledges
        self._escrow = escrow
        self._payees = payees
        self._symbol = symbol
        self._applied = applied
        self._cid = settlement_att.cid
        if applied is not None and self._cid in applied:
            raise EscrowError(f"settlement {self._cid} already executed")
        _mode, self._entries = settlement_entries(outcome_record, campaign_record, pledges)
        self.total = sum(amount for _cid, _payee, amount in self._entries)
        # Validate payees + funding up front (all-or-nothing, same guards as execute_settlement).
        for payee_addr in {payee for _cid, payee, _amount in self._entries}:
            node = payees.get(payee_addr)
            if node is None:
                raise EscrowError(f"no account provided for payee {payee_addr}")
            if node.address != payee_addr:
                raise EscrowError(f"payee node address mismatch for {payee_addr}")
            if payee_addr == escrow.address:
                raise EscrowError("a payee may not be the escrow itself (self-transfer)")
            if node.network != escrow.network:
                raise EscrowError(f"payee {payee_addr} is on a different network than the escrow")
        if escrow.balance(symbol) < self.total:
            raise EscrowError(f"escrow has {escrow.balance(symbol)} {symbol}, settlement needs {self.total}")
        self._cursor = 0
        self._knits: List[Knit] = []

    @property
    def cursor(self) -> int:
        return self._cursor

    def is_complete(self) -> bool:
        return self._cursor >= len(self._entries)

    def step(self, timestamp: int) -> Knit | None:
        """Process the next payout entry; return the applied Knit, or None if already complete."""
        if self.is_complete():
            return None
        _cid, payee_addr, amount = self._entries[self._cursor]
        payee = self._payees[payee_addr]
        proposed = self._escrow.propose(payee.pub, self._symbol, amount, timestamp)
        # the payee independently authorises the proposal before co-signing
        if not validate_payout(proposed, self._att, self._outcome, self._campaign,
                               self._pledges, payee.pub, symbol=self._symbol):
            raise EscrowError("payee refused: proposal does not match the settlement")
        signed = payee.accept(proposed)
        ok, reason = _kw.validate_knit(signed, self._escrow.network)
        if not ok:
            raise EscrowError(f"refusing to apply invalid knit: {reason}")
        self._escrow.apply_sent(signed)
        payee.apply_received(signed)
        self._cursor += 1
        self._knits.append(signed)
        if self.is_complete() and self._applied is not None:
            self._applied.add(self._cid)
        return signed

    def run(self, start_timestamp: int) -> List[Knit]:
        """Drive the session to completion; return all applied Knits (resumes from the cursor)."""
        offset = 0
        while not self.is_complete():
            self.step(start_timestamp + offset)
            offset += 1
        return list(self._knits)
