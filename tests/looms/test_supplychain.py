"""Proofs for the supply-chain loom: only mass-conserving processes are signable.

A process that creates or destroys mass is physically impossible and must be refused
before signing; a balanced one becomes a signed, content-addressed, order-independent
record that weaves into the Web and verifies under its actor's key.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import verify_record
from knitweb.fabric.web import Web
from knitweb.looms.supplychain import (
    Item,
    Line,
    ProcessEvent,
    SupplyChainLoom,
    is_conserved,
    mass_balance,
)


def _repackaging() -> ProcessEvent:
    # 10 bags of 100 g coffee -> 1 case of 1000 g (mass conserved: 1000 == 1000)
    bag = Item("COFFEE-BAG-100", unit_mass_g=100)
    case = Item("COFFEE-CASE-1KG", unit_mass_g=1000)
    return ProcessEvent(inputs=(Line(bag, 10),), outputs=(Line(case, 1),), actor="x")


@pytest.mark.loom
def test_conserved_process_passes():
    actor = "x"
    e = _repackaging()
    assert mass_balance(e) == 0 and is_conserved(e)


@pytest.mark.loom
def test_emit_signs_conserved_event_and_verifies():
    priv, _ = crypto.generate_keypair()
    loom = SupplyChainLoom(priv)
    bag = Item("COFFEE-BAG-100", 100)
    case = Item("COFFEE-CASE-1KG", 1000)
    event = ProcessEvent(inputs=(Line(bag, 10),), outputs=(Line(case, 1),), actor=loom.address)
    att = loom.emit(event)
    assert att.record["conserved"] is True
    assert att.record["total_mass_g"] == 1000
    assert att.verify(author_field="actor")
    assert verify_record(att.record, att.author_pub, att.sig, "actor")
    assert canonical.decode(canonical.encode(att.record)) == att.record


@pytest.mark.loom
def test_mass_creation_is_refused():
    priv, _ = crypto.generate_keypair()
    loom = SupplyChainLoom(priv)
    # 1 bag of 100 g -> 1 case claimed 1000 g: 900 g created from nothing -> refused
    bag = Item("COFFEE-BAG-100", 100)
    case = Item("COFFEE-CASE-1KG", 1000)
    bad = ProcessEvent(inputs=(Line(bag, 1),), outputs=(Line(case, 1),), actor=loom.address)
    assert mass_balance(bad) == 900 and not is_conserved(bad)
    with pytest.raises(ValueError, match="mass not conserved"):
        loom.emit(bad)


@pytest.mark.loom
def test_line_order_does_not_change_content_id():
    priv, _ = crypto.generate_keypair()
    loom = SupplyChainLoom(priv)
    a = Item("A", 250); b = Item("B", 250); out = Item("AB", 1000)
    e1 = ProcessEvent(inputs=(Line(a, 2), Line(b, 2)), outputs=(Line(out, 1),), actor=loom.address)
    e2 = ProcessEvent(inputs=(Line(b, 2), Line(a, 2)), outputs=(Line(out, 1),), actor=loom.address)
    assert loom.to_record(e1) == loom.to_record(e2)
    assert canonical.cid(loom.to_record(e1)) == canonical.cid(loom.to_record(e2))


@pytest.mark.loom
def test_weave_is_content_addressed_and_idempotent():
    priv, _ = crypto.generate_keypair()
    loom = SupplyChainLoom(priv)
    web = Web()
    cid, att = loom.weave(_repackaging_for(loom), web)
    assert cid in web.nodes and web.nodes[cid] == att.record
    cid2, _ = loom.weave(_repackaging_for(loom), web)
    assert cid2 == cid


def _repackaging_for(loom) -> ProcessEvent:
    bag = Item("COFFEE-BAG-100", 100)
    case = Item("COFFEE-CASE-1KG", 1000)
    return ProcessEvent(inputs=(Line(bag, 10),), outputs=(Line(case, 1),), actor=loom.address)


@pytest.mark.loom
def test_tampered_record_fails_verification():
    priv, _ = crypto.generate_keypair()
    loom = SupplyChainLoom(priv)
    att = loom.emit(_repackaging_for(loom))
    forged = dict(att.record, total_mass_g=999999)
    assert not verify_record(forged, att.author_pub, att.sig, "actor")
