"""Proofs for the finance domain loom: only double-entry-balanced entries are signable.

An entry that does not balance (debits != credits) is an accounting error and must be
refused before signing. A balanced one becomes a signed, content-addressed, posting-
order-independent record that weaves into the Web and verifies under the actor's key.
The optional ``settles`` references (audit link to a settlement / priced offer) are
part of the signed record. Scope per docs/research/09-finance-settlement.md.
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


def _balanced_entry(actor: str, settles: tuple[str, ...] = ()) -> LedgerEntry:
    # Spider earns 100 PLS: debit Cash +100, credit Revenue -100 (sum = 0)
    return LedgerEntry(
        postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
        memo="spider earned 100 PLS for compute work",
        actor=actor,
        settles=settles,
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
    att = loom.emit(_balanced_entry(loom.address))
    assert att.record["balanced"] is True
    assert att.record["currency"] == "PLS"
    assert att.record["settles"] == []
    assert att.verify(author_field="actor")
    assert verify_record(att.record, att.author_pub, att.sig, "actor")
    assert canonical.decode(canonical.encode(att.record)) == att.record


@pytest.mark.loom
def test_unbalanced_entry_is_refused():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    bad = LedgerEntry(
        postings=(Posting(_cash(), 100), Posting(_revenue(), -50)),
        memo="oops",
        actor=loom.address,
    )
    assert debit_credit_balance(bad) == 50 and not is_balanced(bad)
    with pytest.raises(ValueError, match="does not balance"):
        loom.emit(bad)


@pytest.mark.loom
def test_posting_order_does_not_change_content_id():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    e1 = LedgerEntry((Posting(_cash(), 100), Posting(_revenue(), -100)), "t", loom.address)
    e2 = LedgerEntry((Posting(_revenue(), -100), Posting(_cash(), 100)), "t", loom.address)
    assert loom.to_record(e1) == loom.to_record(e2)
    assert canonical.cid(loom.to_record(e1)) == canonical.cid(loom.to_record(e2))


@pytest.mark.loom
def test_three_posting_entry_balances():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    expense2 = Account("Expense2", "PLS")
    entry = LedgerEntry(
        postings=(Posting(_expense(), 30), Posting(expense2, 70), Posting(_cash(), -100)),
        memo="split expense",
        actor=loom.address,
    )
    assert is_balanced(entry)
    assert loom.emit(entry).verify(author_field="actor")


@pytest.mark.loom
def test_weave_into_web_is_content_addressed_and_idempotent():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    web = Web()
    cid, att = loom.weave(_balanced_entry(loom.address), web)
    assert cid in web.nodes and web.nodes[cid] == att.record
    assert cid == canonical.cid(att.record)
    cid2, _ = loom.weave(_balanced_entry(loom.address), web)
    assert cid2 == cid  # idempotent


@pytest.mark.loom
def test_tampered_signed_entry_fails_verification():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    att = loom.emit(_balanced_entry(loom.address))
    forged = dict(att.record, memo="i earned way more")
    assert not verify_record(forged, att.author_pub, att.sig, "actor")


@pytest.mark.loom
def test_actor_mismatch_is_refused():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    other = LedgerEntry((Posting(_cash(), 100), Posting(_revenue(), -100)), "m", "pls1someoneelse")
    with pytest.raises(ValueError, match="actor does not match"):
        loom.emit(other)


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
        LedgerEntry((Posting(_cash(), 100),), "single", loom.address)


@pytest.mark.loom
def test_mixed_currency_entry_is_rejected():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    usd = Account("Bank", "USD")
    with pytest.raises(ValueError, match="currency"):
        LedgerEntry((Posting(_cash(), 100), Posting(usd, -100)), "mixed", loom.address)


# ── settles / audit-link references ─────────────────────────────────────────

@pytest.mark.loom
def test_settles_reference_round_trips_and_is_signed():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    att = loom.emit(_balanced_entry(loom.address, settles=("cidSETTLE", "cidOFFER")))
    assert att.record["settles"] == ["cidOFFER", "cidSETTLE"]   # sorted, order-independent
    assert att.verify(author_field="actor")
    assert canonical.decode(canonical.encode(att.record)) == att.record


@pytest.mark.loom
def test_settles_is_part_of_the_content_id():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    without = loom.to_record(_balanced_entry(loom.address))
    with_ref = loom.to_record(_balanced_entry(loom.address, settles=("cidX",)))
    assert canonical.cid(without) != canonical.cid(with_ref)


@pytest.mark.loom
def test_settles_order_does_not_change_content_id():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    a = loom.to_record(_balanced_entry(loom.address, settles=("a", "b")))
    b = loom.to_record(_balanced_entry(loom.address, settles=("b", "a")))
    assert canonical.cid(a) == canonical.cid(b)


@pytest.mark.loom
def test_empty_settles_reference_is_rejected():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    with pytest.raises(ValueError, match="non-empty content-id"):
        _balanced_entry(loom.address, settles=("",))


@pytest.mark.loom
def test_tampering_settles_fails_verification():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    att = loom.emit(_balanced_entry(loom.address, settles=("cidREAL",)))
    forged = dict(att.record, settles=["cidFAKE"])
    assert not verify_record(forged, att.author_pub, att.sig, "actor")
