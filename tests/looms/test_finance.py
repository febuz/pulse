"""Proofs for the finance domain loom: only double-entry-balanced entries are signable.

An entry that does not balance (debits != credits) is an accounting error and must be
refused before signing. A balanced one becomes a signed, content-addressed, posting-
order-independent record that weaves into the Web and verifies under the actor's key.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import verify_record
from knitweb.fabric.web import Web
from knitweb.looms.finance import (
    Account,
    FinanceLoom,
    LedgerEntry,
    Posting,
    debit_credit_balance,
    is_balanced,
)


def _cash() -> Account:
    return Account("Cash", "PLS")


def _revenue() -> Account:
    return Account("Revenue", "PLS")


def _expense() -> Account:
    return Account("Expense", "PLS")


def _balanced_entry(actor: str) -> LedgerEntry:
    # Spider earns 100 PLS: debit Cash +100, credit Revenue -100 (sum = 0)
    return LedgerEntry(
        postings=(
            Posting(_cash(), 100),
            Posting(_revenue(), -100),
        ),
        memo="spider earned 100 PLS for compute work",
        actor=actor,
    )


@pytest.mark.loom
def test_balanced_entry_passes_checks():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    entry = _balanced_entry(loom.address)
    assert debit_credit_balance(entry) == 0
    assert is_balanced(entry)


@pytest.mark.loom
def test_emit_signs_balanced_entry_and_is_verifiable():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    entry = _balanced_entry(loom.address)
    att = loom.emit(entry)
    assert att.record["balanced"] is True
    assert att.record["currency"] == "PLS"
    assert att.verify(author_field="actor")
    assert verify_record(att.record, att.author_pub, att.sig, "actor")
    # signed record round-trips through canonical CBOR (integer-only path)
    assert canonical.decode(canonical.encode(att.record)) == att.record


@pytest.mark.loom
def test_unbalanced_entry_is_refused():
    # Debit 100 with no matching credit (net = 100 != 0) -> refused
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    bad = LedgerEntry(
        postings=(Posting(_cash(), 100), Posting(_revenue(), -50)),
        memo="oops",
        actor=loom.address,
    )
    assert debit_credit_balance(bad) == 50
    assert not is_balanced(bad)
    with pytest.raises(ValueError, match="does not balance"):
        loom.emit(bad)


@pytest.mark.loom
def test_posting_order_does_not_change_content_id():
    # The same entry written with postings in swapped order must yield the same CID.
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    e1 = LedgerEntry(
        postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
        memo="test",
        actor=loom.address,
    )
    e2 = LedgerEntry(
        postings=(Posting(_revenue(), -100), Posting(_cash(), 100)),
        memo="test",
        actor=loom.address,
    )
    assert loom.to_record(e1) == loom.to_record(e2)
    assert canonical.cid(loom.to_record(e1)) == canonical.cid(loom.to_record(e2))


@pytest.mark.loom
def test_three_posting_entry_balances():
    # Expense 30+70=100 paid from Cash: debit Expense 30, debit Expense2 70, credit Cash -100
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    expense2 = Account("Expense2", "PLS")
    entry = LedgerEntry(
        postings=(
            Posting(_expense(), 30),
            Posting(expense2, 70),
            Posting(_cash(), -100),
        ),
        memo="split expense",
        actor=loom.address,
    )
    assert is_balanced(entry)
    att = loom.emit(entry)
    assert att.verify(author_field="actor")


@pytest.mark.loom
def test_weave_into_web_is_content_addressed_and_idempotent():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    web = Web()
    entry = _balanced_entry(loom.address)
    cid, att = loom.weave(entry, web)
    assert cid in web.nodes
    assert web.nodes[cid] == att.record
    assert cid == canonical.cid(att.record)
    cid2, _ = loom.weave(entry, web)
    assert cid2 == cid  # idempotent


@pytest.mark.loom
def test_tampered_signed_entry_fails_verification():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    att = loom.emit(_balanced_entry(loom.address))
    forged = dict(att.record, memo="i earned way more")
    assert not verify_record(forged, att.author_pub, att.sig, "actor")


@pytest.mark.loom
def test_zero_amount_posting_is_rejected():
    with pytest.raises(ValueError, match="non-zero"):
        Posting(_cash(), 0)


@pytest.mark.loom
def test_float_amount_is_rejected():
    with pytest.raises(TypeError, match="int"):
        Posting(_cash(), 10.5)  # type: ignore[arg-type]


@pytest.mark.loom
def test_single_posting_entry_is_rejected():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    with pytest.raises(ValueError, match="at least two"):
        LedgerEntry(
            postings=(Posting(_cash(), 100),),
            memo="single",
            actor=loom.address,
        )


@pytest.mark.loom
def test_mixed_currency_entry_is_rejected():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    usd = Account("Bank", "USD")
    with pytest.raises(ValueError, match="currency"):
        LedgerEntry(
            postings=(Posting(_cash(), 100), Posting(usd, -100)),
            memo="mixed",
            actor=loom.address,
        )
