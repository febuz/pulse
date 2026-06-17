"""Property tests for the Finance Loom: invoices, double-entry accounting, settlement."""

import pytest

from knitweb.fabric.attest import verify_record
from knitweb.fabric.web import Web
from knitweb.ledger.node import AccountNode
from knitweb.looms.finance import FinanceLoom, Invoice, JournalEntry, JournalLine


def _pair(pls: int = 10_000) -> tuple[AccountNode, AccountNode]:
    """Two funded AccountNodes for testing (payer, payee)."""
    payer = AccountNode(genesis_balances={"PLS": pls})
    payee = AccountNode(genesis_balances={"PLS": 0})
    return payer, payee


# ---------------------------------------------------------------------------
# Invoice construction & validation
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_invoice_valid_construction():
    payer, payee = _pair()
    inv = Invoice(
        issuer=payee.address, payer=payer.address,
        amount=500, issue_epoch=1, due_epoch=5,
    )
    assert inv.amount == 500
    assert inv.cid  # content-addressable


@pytest.mark.property
def test_invoice_cid_is_deterministic():
    payer, payee = _pair()
    a = Invoice(issuer=payee.address, payer=payer.address, amount=1000, issue_epoch=0, due_epoch=10)
    b = Invoice(issuer=payee.address, payer=payer.address, amount=1000, issue_epoch=0, due_epoch=10)
    assert a.cid == b.cid


@pytest.mark.property
def test_invoice_rejects_zero_amount():
    with pytest.raises(ValueError):
        Invoice(issuer="pls1a", payer="pls1b", amount=0, issue_epoch=0, due_epoch=1)


@pytest.mark.property
def test_invoice_rejects_negative_amount():
    with pytest.raises(ValueError):
        Invoice(issuer="pls1a", payer="pls1b", amount=-1, issue_epoch=0, due_epoch=1)


@pytest.mark.property
def test_invoice_rejects_float_amount():
    with pytest.raises(TypeError):
        Invoice(issuer="pls1a", payer="pls1b", amount=1.5, issue_epoch=0, due_epoch=1)  # type: ignore


@pytest.mark.property
def test_invoice_rejects_overdue_epoch():
    with pytest.raises(ValueError):
        Invoice(issuer="pls1a", payer="pls1b", amount=1, issue_epoch=5, due_epoch=4)


@pytest.mark.property
def test_invoice_no_float_in_record():
    payer, payee = _pair()
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=200, issue_epoch=0, due_epoch=1)
    for v in inv.to_record().values():
        assert not isinstance(v, float), f"float in invoice record: {v}"


@pytest.mark.property
def test_invoice_same_epoch_is_valid():
    """issue_epoch == due_epoch is allowed (same-epoch payment)."""
    Invoice(issuer="pls1a", payer="pls1b", amount=1, issue_epoch=3, due_epoch=3)


# ---------------------------------------------------------------------------
# JournalLine construction
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_journal_line_rejects_float():
    with pytest.raises(TypeError):
        JournalLine(account="pls1a", symbol="PLS", amount=1.5)  # type: ignore


# ---------------------------------------------------------------------------
# JournalEntry validation
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_journal_entry_balanced():
    lines = (
        JournalLine(account="pls1a", symbol="PLS", amount=-500),
        JournalLine(account="pls1b", symbol="PLS", amount=500),
    )
    ok, reason = JournalEntry(description="t", epoch=0, lines=lines).validate()
    assert ok, reason


@pytest.mark.property
def test_journal_entry_unbalanced_fails():
    lines = (
        JournalLine(account="pls1a", symbol="PLS", amount=-400),
        JournalLine(account="pls1b", symbol="PLS", amount=500),
    )
    ok, reason = JournalEntry(description="t", epoch=0, lines=lines).validate()
    assert not ok
    assert "PLS" in reason


@pytest.mark.property
def test_journal_entry_empty_fails():
    ok, _ = JournalEntry(description="t", epoch=0, lines=()).validate()
    assert not ok


@pytest.mark.property
def test_journal_entry_multi_symbol_balanced():
    lines = (
        JournalLine(account="pls1a", symbol="PLS", amount=-100),
        JournalLine(account="pls1b", symbol="PLS", amount=100),
        JournalLine(account="pls1a", symbol="ETH", amount=-50),
        JournalLine(account="pls1b", symbol="ETH", amount=50),
    )
    ok, _ = JournalEntry(description="t", epoch=0, lines=lines).validate()
    assert ok


@pytest.mark.property
def test_journal_entry_multi_symbol_partial_unbalanced():
    lines = (
        JournalLine(account="pls1a", symbol="PLS", amount=-100),
        JournalLine(account="pls1b", symbol="PLS", amount=100),
        JournalLine(account="pls1a", symbol="ETH", amount=-50),
        JournalLine(account="pls1b", symbol="ETH", amount=40),  # short by 10
    )
    ok, reason = JournalEntry(description="t", epoch=0, lines=lines).validate()
    assert not ok
    assert "ETH" in reason


@pytest.mark.property
def test_journal_entry_cid_deterministic():
    lines = (
        JournalLine(account="pls1a", symbol="PLS", amount=-1),
        JournalLine(account="pls1b", symbol="PLS", amount=1),
    )
    a = JournalEntry(description="d", epoch=0, lines=lines)
    b = JournalEntry(description="d", epoch=0, lines=lines)
    assert a.cid == b.cid


@pytest.mark.property
def test_journal_entry_no_float_in_record():
    lines = (
        JournalLine(account="pls1a", symbol="PLS", amount=-1),
        JournalLine(account="pls1b", symbol="PLS", amount=1),
    )
    rec = JournalEntry(description="d", epoch=0, lines=lines).to_record()

    def _check(val: object) -> None:
        assert not isinstance(val, float), f"float found: {val}"
        if isinstance(val, dict):
            for v in val.values():
                _check(v)
        elif isinstance(val, list):
            for v in val:
                _check(v)

    _check(rec)


# ---------------------------------------------------------------------------
# FinanceLoom.issue
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_finance_loom_issue_weaves_invoice():
    payer, payee = _pair()
    web = Web()
    loom = FinanceLoom(web)
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=300, issue_epoch=0, due_epoch=5)
    cid, att = loom.issue(inv, payee.priv)
    assert web.get(cid) == inv.to_record()
    assert att.verify(author_field="issuer")


@pytest.mark.property
def test_finance_loom_issue_is_idempotent():
    payer, payee = _pair()
    web = Web()
    loom = FinanceLoom(web)
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=300, issue_epoch=0, due_epoch=5)
    cid1, _ = loom.issue(inv, payee.priv)
    cid2, _ = loom.issue(inv, payee.priv)
    assert cid1 == cid2
    assert web.size[0] == 1  # idempotent weave


@pytest.mark.property
def test_finance_loom_issue_sig_covers_record():
    payer, payee = _pair()
    web = Web()
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=300, issue_epoch=0, due_epoch=5)
    _, att = FinanceLoom(web).issue(inv, payee.priv)
    assert att.author_pub == payee.pub
    assert verify_record(inv.to_record(), payee.pub, att.sig, author_field="issuer")


@pytest.mark.property
def test_finance_loom_issue_rejects_wrong_key():
    """Signing with the payer's key (not the issuer's) must fail."""
    payer, payee = _pair()
    web = Web()
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=100, issue_epoch=0, due_epoch=1)
    with pytest.raises(ValueError):
        FinanceLoom(web).issue(inv, payer.priv)   # wrong key


# ---------------------------------------------------------------------------
# FinanceLoom.settle
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_finance_loom_settle_transfers_balance():
    payer, payee = _pair(pls=10_000)
    _, _ = FinanceLoom(Web()).settle(
        Invoice(issuer=payee.address, payer=payer.address, amount=3_000, issue_epoch=0, due_epoch=10),
        payer, payee, timestamp=1,
    )
    assert payer.balance("PLS") == 7_000
    assert payee.balance("PLS") == 3_000


@pytest.mark.property
def test_finance_loom_settle_weaves_journal_and_links():
    payer, payee = _pair(pls=5_000)
    web = Web()
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=1_000, issue_epoch=0, due_epoch=10)
    invoice_cid, journal_cid = FinanceLoom(web).settle(inv, payer, payee, timestamp=1)
    assert web.get(invoice_cid) == inv.to_record()
    assert web.get(journal_cid)["kind"] == "journal-entry"
    assert journal_cid in web.neighbors(invoice_cid, rel="settled-by")


@pytest.mark.property
def test_finance_loom_settle_journal_is_balanced():
    payer, payee = _pair(pls=5_000)
    web = Web()
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=2_500, issue_epoch=0, due_epoch=5)
    _, journal_cid = FinanceLoom(web).settle(inv, payer, payee, timestamp=1)
    net = sum(ln["amount"] for ln in web.get(journal_cid)["lines"])
    assert net == 0   # debits == credits


@pytest.mark.property
def test_finance_loom_settle_insufficient_balance_raises():
    payer, payee = _pair(pls=100)
    with pytest.raises(ValueError):
        FinanceLoom(Web()).settle(
            Invoice(issuer=payee.address, payer=payer.address, amount=1_000, issue_epoch=0, due_epoch=5),
            payer, payee, timestamp=1,
        )


@pytest.mark.property
def test_finance_loom_settle_exact_balance_succeeds():
    """Settling exactly the full balance leaves the payer at zero."""
    payer, payee = _pair(pls=500)
    FinanceLoom(Web()).settle(
        Invoice(issuer=payee.address, payer=payer.address, amount=500, issue_epoch=0, due_epoch=1),
        payer, payee, timestamp=1,
    )
    assert payer.balance("PLS") == 0
    assert payee.balance("PLS") == 500


@pytest.mark.property
def test_finance_loom_pls_conservation():
    """Total PLS is conserved across issue + settle."""
    payer, payee = _pair(pls=8_000)
    web = Web()
    loom = FinanceLoom(web)
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=3_500, issue_epoch=1, due_epoch=4, memo="GPU compile job")

    loom.issue(inv, payee.priv)
    loom.settle(inv, payer, payee, timestamp=100)

    assert payer.balance("PLS") + payee.balance("PLS") == 8_000


@pytest.mark.property
def test_finance_loom_full_cycle_web_graph():
    """End-to-end: issue + settle produces a traversable Web graph."""
    payer, payee = _pair(pls=5_000)
    web = Web()
    loom = FinanceLoom(web)
    inv = Invoice(issuer=payee.address, payer=payer.address, amount=1_500, issue_epoch=1, due_epoch=3)

    issue_cid, att = loom.issue(inv, payee.priv)
    assert att.verify(author_field="issuer")

    settle_cid, journal_cid = loom.settle(inv, payer, payee, timestamp=200)

    # invoice CID is the same in both calls (idempotent)
    assert issue_cid == settle_cid

    # journal is reachable from invoice
    reachable = web.traverse(issue_cid, depth=1, rels={"settled-by"})
    assert journal_cid in reachable
