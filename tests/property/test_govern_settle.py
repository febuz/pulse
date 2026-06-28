"""Proofs for governance settlement: a quantised analytics decision becomes an integer Knit."""

import pytest

from knitweb.govern import SettlementKind, SettlementOrder, settle
from knitweb.ledger.node import AccountNode


@pytest.mark.property
def test_float_amount_rejected_at_the_seam():
    with pytest.raises(TypeError, match="amount must be int"):
        SettlementOrder(SettlementKind.COUPON, 50.0)  # type: ignore[arg-type]


@pytest.mark.property
def test_bool_amount_rejected():
    with pytest.raises(TypeError, match="amount must be int"):
        SettlementOrder(SettlementKind.COUPON, True)  # type: ignore[arg-type]


@pytest.mark.property
def test_negative_amount_rejected():
    with pytest.raises(ValueError, match="amount must be >= 0"):
        SettlementOrder(SettlementKind.REDEMPTION, -1)


@pytest.mark.property
def test_kind_must_be_settlement_kind():
    with pytest.raises(TypeError, match="kind must be a SettlementKind"):
        SettlementOrder("COUPON", 1)  # type: ignore[arg-type]


@pytest.mark.property
def test_coupon_settles_native_pls():
    issuer = AccountNode.from_seed("issuer", genesis_balances={"PLS": 1000})
    holder = AccountNode.from_seed("holder")
    order = SettlementOrder(SettlementKind.COUPON, 50, beat=1, ref="rate")
    knit = settle(order, issuer, holder, timestamp=1)
    assert knit.symbol == "PLS"
    assert knit.amount == 50
    assert issuer.balance("PLS") == 950
    assert holder.balance("PLS") == 50
    assert knit.from_sig is not None and knit.to_sig is not None


@pytest.mark.property
def test_redemption_settles_face_in_pls():
    issuer = AccountNode.from_seed("issuer", genesis_balances={"PLS": 5000})
    holder = AccountNode.from_seed("holder")
    order = SettlementOrder(SettlementKind.REDEMPTION, 1000, beat=3, ref="rate")
    settle(order, issuer, holder, timestamp=3)
    assert holder.balance("PLS") == 1000
    assert issuer.balance("PLS") == 4000


@pytest.mark.property
def test_conversion_settles_underlying_units_not_pls():
    issuer = AccountNode.from_seed("issuer", genesis_balances={"PLS": 100, "MOL": 10})
    holder = AccountNode.from_seed("holder")
    order = SettlementOrder(SettlementKind.CONVERSION, 10, symbol="MOL", beat=3, ref="rate")
    knit = settle(order, issuer, holder, timestamp=3)
    assert knit.symbol == "MOL"
    assert holder.balance("MOL") == 10
    assert issuer.balance("MOL") == 0
    assert issuer.balance("PLS") == 100


@pytest.mark.property
def test_insufficient_balance_is_refused():
    issuer = AccountNode.from_seed("issuer", genesis_balances={"PLS": 10})
    holder = AccountNode.from_seed("holder")
    order = SettlementOrder(SettlementKind.REDEMPTION, 1000)
    with pytest.raises(ValueError):
        settle(order, issuer, holder, timestamp=1)
    assert issuer.balance("PLS") == 10
    assert holder.balance("PLS") == 0


@pytest.mark.property
def test_nonce_advances_across_serial_settlements():
    issuer = AccountNode.from_seed("issuer", genesis_balances={"PLS": 1000})
    holder = AccountNode.from_seed("holder")
    n0 = issuer.nonce
    settle(SettlementOrder(SettlementKind.COUPON, 50, beat=1), issuer, holder, timestamp=1)
    settle(SettlementOrder(SettlementKind.COUPON, 50, beat=2), issuer, holder, timestamp=2)
    assert issuer.nonce == n0 + 2
    assert holder.balance("PLS") == 100


@pytest.mark.property
def test_order_has_stable_cid():
    a = SettlementOrder(SettlementKind.COUPON, 50, beat=1, ref="rate")
    b = SettlementOrder(SettlementKind.COUPON, 50, beat=1, ref="rate")
    c = SettlementOrder(SettlementKind.COUPON, 51, beat=1, ref="rate")
    assert a.cid == b.cid
    assert a.cid != c.cid
    assert isinstance(a.cid, str) and a.cid.startswith("b")


@pytest.mark.property
def test_order_record_is_float_free():
    order = SettlementOrder(SettlementKind.CONVERSION, 10, symbol="MOL", beat=3, ref="rate")
    rec = order.to_record()
    assert rec["amount"] == 10 and isinstance(rec["amount"], int)
    assert rec["settle_kind"] == "CONVERSION"
    assert rec["symbol"] == "MOL"
