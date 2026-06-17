"""Finance loom — signed invoices and double-entry accounting on the fabric.

The Finance Loom brings financial-settlement semantics to Knitweb, following the
same soundness principles as the rest of the system: integer-only amounts
(PLS-wei), canonical CBOR encoding, and ECDSA signatures via ``fabric.attest``.

Two first-class primitives:

  * Invoice      — a signed payment request from an issuer to a payer.  The
                   amount is a strictly-positive integer PLS-wei; the due_epoch
                   is a forward Pulse epoch.  The issuer ECDSA-signs the record
                   (via ``fabric.attest``); the Web stores the record and links
                   it to the settling Knit once paid.

  * JournalEntry — a double-entry accounting record: a set of (account, symbol,
                   amount) lines that must sum to zero per symbol (debits ==
                   credits, net == 0). This is the accounting audit trail of any
                   settlement. All amounts are integer PLS-wei; floats are
                   rejected by the constructor.

``FinanceLoom`` ties these together: it validates, signs, and weaves invoices
into the Web, then settles them by driving an AccountNode Knit transfer +
weaving the balancing JournalEntry that audits the flow.

No floats, no premine, no speculation — the loom enforces accounting invariants
at write time, so every peer can re-check them deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import canonical
from ...fabric.attest import Attestation, attest, verify_record
from ...fabric.web import Web
from ...ledger.node import AccountNode

__all__ = [
    "Invoice",
    "JournalLine",
    "JournalEntry",
    "FinanceLoom",
]


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Invoice:
    """A signed payment request from issuer to payer.

    All monetary values are integer PLS-wei. Epochs are integer Pulse epoch
    numbers. Floats are rejected at construction — no float can enter the
    accounting or signing path.
    """

    issuer: str        # PLS address of the payee (spider / service provider)
    payer: str         # PLS address of the debtor (consumer)
    amount: int        # PLS-wei; must be strictly positive
    issue_epoch: int   # Pulse epoch when this invoice is created
    due_epoch: int     # Pulse epoch by which payment is expected; >= issue_epoch
    memo: str = ""     # free-form description (not part of the conservation gate)

    def __post_init__(self) -> None:
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise TypeError("Invoice.amount must be int")
        if self.amount <= 0:
            raise ValueError("Invoice.amount must be positive")
        if isinstance(self.due_epoch, bool) or not isinstance(self.due_epoch, int):
            raise TypeError("Invoice.due_epoch must be int")
        if self.due_epoch < self.issue_epoch:
            raise ValueError("Invoice.due_epoch must be >= issue_epoch")

    def to_record(self) -> dict:
        return {
            "kind": "invoice",
            "issuer": self.issuer,
            "payer": self.payer,
            "amount": self.amount,
            "issue_epoch": self.issue_epoch,
            "due_epoch": self.due_epoch,
            "memo": self.memo,
        }

    @property
    def cid(self) -> str:
        """Content-address of the unsigned invoice record."""
        return canonical.cid(self.to_record())


# ---------------------------------------------------------------------------
# JournalEntry (double-entry accounting)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JournalLine:
    """One line of a double-entry journal: an account, a symbol, and an amount.

    Positive amount = credit (value arriving); negative = debit (value leaving).
    All amounts are integer PLS-wei; floats are rejected at construction.
    """

    account: str    # PLS address or account label
    symbol: str     # token symbol, e.g. "PLS"
    amount: int     # signed integer PLS-wei (no floats)

    def __post_init__(self) -> None:
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise TypeError("JournalLine.amount must be int")


@dataclass(frozen=True)
class JournalEntry:
    """A double-entry accounting record: lines must sum to zero per symbol.

    The zero-sum invariant is conservation of value: total debits == total credits.
    ``FinanceLoom`` refuses to weave entries that fail ``validate()``.
    """

    description: str
    epoch: int
    lines: tuple[JournalLine, ...]

    def validate(self) -> tuple[bool, str]:
        """Return (True, "") if all symbols net to zero; (False, reason) otherwise."""
        if not self.lines:
            return False, "journal entry has no lines"
        totals: dict[str, int] = {}
        for line in self.lines:
            totals[line.symbol] = totals.get(line.symbol, 0) + line.amount
        for sym, total in totals.items():
            if total != 0:
                return False, f"symbol {sym!r} does not balance: net {total:+d}"
        return True, ""

    def to_record(self) -> dict:
        return {
            "kind": "journal-entry",
            "description": self.description,
            "epoch": self.epoch,
            "lines": [
                {"account": ln.account, "symbol": ln.symbol, "amount": ln.amount}
                for ln in self.lines
            ],
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())


# ---------------------------------------------------------------------------
# FinanceLoom
# ---------------------------------------------------------------------------

class FinanceLoom:
    """Validates, signs, and settles invoices on the fabric.

    All state mutations go through the Web (content-addressed records) and the
    AccountNode ledger (integer Knit transfers). The loom adds no state of its
    own — it is a stateless validator + orchestrator over those two surfaces.
    """

    def __init__(self, web: Web, network: int = 1) -> None:
        self.web = web
        self.network = network

    def issue(self, invoice: Invoice, issuer_priv: str) -> tuple[str, Attestation]:
        """Sign and weave an invoice into the Web.

        The issuer's private key must correspond to ``invoice.issuer`` (the PLS
        address); ``fabric.attest`` enforces this. Returns ``(invoice_cid,
        attestation)``. Weaving is idempotent — re-issuing the same invoice is
        a no-op on the Web and returns the same CID.
        """
        att = attest(invoice.to_record(), issuer_priv, author_field="issuer")
        invoice_cid = self.web.weave(invoice.to_record())
        return invoice_cid, att

    def settle(
        self,
        invoice: Invoice,
        payer_node: AccountNode,
        payee_node: AccountNode,
        timestamp: int,
    ) -> tuple[str, str]:
        """Pay an invoice: transfer PLS and record the accounting entry in the Web.

        Returns ``(invoice_cid, journal_cid)``.  Raises ``ValueError`` if the
        payer's PLS balance is insufficient.

        The double-entry JournalEntry that is woven debits the payer and credits
        the issuer by exactly ``invoice.amount`` PLS-wei, maintaining the
        zero-sum invariant (debits == credits).  The Web links the invoice to its
        journal entry via a "settled-by" edge so the full audit trail is
        graph-traversable.
        """
        bal = payer_node.balance("PLS")
        if bal < invoice.amount:
            raise ValueError(
                f"payer balance {bal} PLS-wei < invoice amount {invoice.amount} PLS-wei"
            )

        knit = payer_node.transfer_to(payee_node, "PLS", invoice.amount, timestamp)

        entry = JournalEntry(
            description=f"invoice:{invoice.cid[:12]} knit:{knit.id[:12]}",
            epoch=timestamp,
            lines=(
                JournalLine(account=invoice.payer, symbol="PLS", amount=-invoice.amount),
                JournalLine(account=invoice.issuer, symbol="PLS", amount=invoice.amount),
            ),
        )
        ok, reason = entry.validate()
        assert ok, f"internal: journal entry unbalanced after settle: {reason}"

        invoice_cid = self.web.weave(invoice.to_record())
        journal_cid = self.web.weave(entry.to_record())
        self.web.link(invoice_cid, journal_cid, "settled-by")
        return invoice_cid, journal_cid
