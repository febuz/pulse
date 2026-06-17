"""Finance loom — emit signed, double-entry-balanced ledger entries into the Web.

A finance loom models double-entry bookkeeping: every ledger entry is a set of
postings whose amounts (integers in the account's base currency unit) sum to zero
— debits equal credits. An entry that does not balance is an accounting error and
is refused before any signature is produced. This is the same soundness discipline
as the chemistry loom (element balance) and supply-chain loom (mass conservation),
applied to the double-entry invariant.

All amounts are integers (no floats), so records round-trip through canonical CBOR.
A balanced entry becomes a signed, content-addressed ``finance-entry`` record woven
into the Web by its author.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web

__all__ = [
    "Account",
    "Posting",
    "LedgerEntry",
    "FinanceLoom",
    "debit_credit_balance",
    "is_balanced",
]


@dataclass(frozen=True)
class Account:
    """A named account with a base-unit currency denomination (e.g. "PLS", "USD")."""

    name: str
    currency: str


@dataclass(frozen=True)
class Posting:
    """An integer amount posted to an account.

    Sign convention (standard double-entry):
      positive  → debit  (increases assets / expenses)
      negative  → credit (increases liabilities / equity / income)
    """

    account: Account
    amount: int

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise TypeError("posting amount must be int")
        if self.amount == 0:
            raise ValueError("posting amount must be non-zero")


@dataclass(frozen=True)
class LedgerEntry:
    """A balanced set of postings: debits must equal credits (amounts sum to zero).

    ``memo`` is a human-readable description; it is part of the signed record so
    changing it changes the CID. All postings must use the same currency.
    """

    postings: tuple[Posting, ...]
    memo: str
    actor: str  # PLS address of the signing entity

    def __post_init__(self) -> None:
        if not self.postings:
            raise ValueError("a ledger entry needs at least two postings")
        if len(self.postings) < 2:
            raise ValueError("a ledger entry needs at least two postings")
        currencies = {p.account.currency for p in self.postings}
        if len(currencies) > 1:
            raise ValueError(
                f"all postings in one entry must share a currency; got {currencies}"
            )


# ---------------------------------------------------------------------------
# Invariant checks
# ---------------------------------------------------------------------------

def debit_credit_balance(entry: LedgerEntry) -> int:
    """Net sum of all posting amounts. Balanced ⇔ 0 (debits == credits)."""
    return sum(p.amount for p in entry.postings)


def is_balanced(entry: LedgerEntry) -> bool:
    """True iff debits equal credits (all amounts sum to zero)."""
    return debit_credit_balance(entry) == 0


# ---------------------------------------------------------------------------
# The loom
# ---------------------------------------------------------------------------

def _sorted_postings(postings: tuple[Posting, ...]) -> list[Posting]:
    """Canonical posting order (by account name then amount) so the same entry
    written in any order produces one content id."""
    return sorted(postings, key=lambda p: (p.account.name, p.amount))


class FinanceLoom:
    """Emits signed, double-entry-balanced ledger entries for one signing entity."""

    KIND = "finance-entry"

    def __init__(self, actor_priv: str) -> None:
        self._priv = actor_priv
        self.actor_pub = crypto.public_from_private(actor_priv)
        self.address = crypto.address(self.actor_pub)

    def to_record(self, entry: LedgerEntry) -> dict:
        """Build the integer-only, canonical-encodable record for a ledger entry."""
        if entry.actor != self.address:
            raise ValueError("ledger entry actor does not match signing key")
        currency = entry.postings[0].account.currency
        postings = _sorted_postings(entry.postings)
        record = {
            "kind": self.KIND,
            "currency": currency,
            "memo": entry.memo,
            "postings": [
                {"account": p.account.name, "amount": p.amount}
                for p in postings
            ],
            "actor": self.address,
            "balanced": True,
        }
        canonical.encode(record)  # fail fast on any non-canonical content
        return record

    def emit(self, entry: LedgerEntry) -> Attestation:
        """Validate double-entry balance, then sign the entry. Raises if unbalanced."""
        net = debit_credit_balance(entry)
        if net != 0:
            raise ValueError(f"ledger entry does not balance (net {net}), cannot sign")
        return attest(self.to_record(entry), self._priv, author_field="actor")

    def weave(self, entry: LedgerEntry, web: Web) -> tuple[str, Attestation]:
        """Emit a signed entry and weave it into *web*; return (cid, attestation)."""
        att = self.emit(entry)
        return web.weave(att.record), att
