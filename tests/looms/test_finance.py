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
def test_entry_actor_must_match_signing_key():
    priv, _ = crypto.generate_keypair()
    other_priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    other = FinanceLoom(other_priv)
    entry = _balanced_entry(other.address)
    with pytest.raises(ValueError, match="actor"):
        loom.emit(entry)


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


# ---------------------------------------------------------------------------
# settles: the audit link closing the allocation -> priced-offer -> settlement loop
# ---------------------------------------------------------------------------

@pytest.mark.loom
def test_no_settles_omits_the_key_and_keeps_plain_cid():
    # An entry with no settlement references must produce exactly the same record
    # (and CID) as before the field existed: the key is absent, not an empty list.
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    rec = loom.to_record(_balanced_entry(loom.address))
    assert "settles" not in rec


@pytest.mark.loom
def test_settles_references_are_signed_into_the_record():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    alloc_cid = canonical.cid({"kind": "operational-allocation", "n": 1})
    offer_cid = canonical.cid({"kind": "resource-item", "n": 2})
    entry = LedgerEntry(
        postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
        memo="settle GPU lease",
        actor=loom.address,
        settles=(alloc_cid, offer_cid),
    )
    att = loom.emit(entry)
    assert set(att.record["settles"]) == {alloc_cid, offer_cid}
    # the reference is inside the signed envelope: tampering with it breaks verify
    forged = dict(att.record, settles=[offer_cid])
    assert not verify_record(forged, att.author_pub, att.sig, "actor")
    assert att.verify(author_field="actor")


@pytest.mark.loom
def test_settles_order_and_duplicates_do_not_change_content_id():
    priv, _ = crypto.generate_keypair()
    loom = FinanceLoom(priv)
    a = canonical.cid({"r": "a"})
    b = canonical.cid({"r": "b"})
    e1 = LedgerEntry(
        postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
        memo="x",
        actor=loom.address,
        settles=(a, b),
    )
    e2 = LedgerEntry(
        postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
        memo="x",
        actor=loom.address,
        settles=(b, a, a),  # reversed + duplicate
    )
    assert loom.to_record(e1) == loom.to_record(e2)
    assert canonical.cid(loom.to_record(e1)) == canonical.cid(loom.to_record(e2))


@pytest.mark.loom
def test_empty_or_nonstring_settles_reference_is_rejected():
    with pytest.raises(TypeError, match="CID string"):
        LedgerEntry(
            postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
            memo="bad",
            actor="pls1whatever",
            settles=("",),
        )
    with pytest.raises(TypeError, match="CID string"):
        LedgerEntry(
            postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
            memo="bad",
            actor="pls1whatever",
            settles=(123,),  # type: ignore[arg-type]
        )


@pytest.mark.loom
def test_malformed_cid_reference_is_rejected():
    # A non-CID string (wrong prefix / non-base32 chars / too short) is caught at
    # write time rather than left dangling. Real CIDs (b... base32-lower) pass.
    good = canonical.cid({"r": "ok"})
    assert good.startswith("b")
    for bad in ("not-a-cid", "Qm" + "a" * 44, "b", "babc", "b!!!notbase32!!!xxxx"):
        with pytest.raises(ValueError, match="CIDv1"):
            LedgerEntry(
                postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
                memo="bad",
                actor="pls1whatever",
                settles=(bad,),
            )
    # a genuine CID is accepted (actor mismatch is checked later, at emit time)
    LedgerEntry(
        postings=(Posting(_cash(), 100), Posting(_revenue(), -100)),
        memo="ok",
        actor="pls1whatever",
        settles=(good,),
    )
