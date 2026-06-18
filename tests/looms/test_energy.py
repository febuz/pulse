"""Proofs for the energy loom: only energy-conserving dispatch events are signable.

An event that creates or destroys energy is physically impossible and must be refused before
signing; a balanced one becomes a signed, content-addressed, order-independent ``energy-balance``
record that weaves into the Web and verifies under its operator's key. A discharging battery
(negative ``storage_delta``) legitimately lets generation fall below load — still conserved.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import verify_record
from knitweb.fabric.web import Web
from knitweb.looms.energy import (
    DispatchEvent,
    EnergyLoom,
    Flow,
    energy_balance,
    is_conserved,
)


def _balanced(actor="x"):
    # 1000 Wh solar -> 900 Wh load + 100 Wh line loss + 0 storage  (1000 == 900+100+0)
    return DispatchEvent(
        generation=(Flow("solar", 1000),),
        consumption=(Flow("town", 900),),
        losses=(Flow("transmission", 100),),
        storage_delta=0,
        actor=actor,
    )


# ── 1. The conservation invariant ────────────────────────────────────────────

@pytest.mark.loom
def test_conserved_event_passes():
    e = _balanced()
    assert energy_balance(e) == 0 and is_conserved(e)


@pytest.mark.loom
def test_imbalanced_event_detected():
    e = DispatchEvent(
        generation=(Flow("solar", 1000),),
        consumption=(Flow("town", 950),),
        losses=(Flow("transmission", 100),),     # 1000 != 950 + 100
        storage_delta=0,
        actor="x",
    )
    assert energy_balance(e) == -50 and not is_conserved(e)


@pytest.mark.loom
def test_battery_discharge_makes_up_the_shortfall():
    # generation 600 < load 1000 + loss 50, because the battery discharges 450 (delta = -450)
    # 600 - 1000 - 50 - (-450) == 0
    e = DispatchEvent(
        generation=(Flow("solar", 600),),
        consumption=(Flow("town", 1000),),
        losses=(Flow("inverter", 50),),
        storage_delta=-450,
        actor="x",
    )
    assert is_conserved(e)


@pytest.mark.loom
def test_battery_charge_absorbs_the_surplus():
    # generation 1000 > load 700 + loss 100, surplus 200 charges the battery (delta = +200)
    e = DispatchEvent(
        generation=(Flow("wind", 1000),),
        consumption=(Flow("town", 700),),
        losses=(Flow("transmission", 100),),
        storage_delta=200,
        actor="x",
    )
    assert is_conserved(e)


@pytest.mark.loom
def test_multi_source_multi_load_balance():
    e = DispatchEvent(
        generation=(Flow("solar", 800), Flow("wind", 1200)),
        consumption=(Flow("town", 1500), Flow("factory", 400)),
        losses=(Flow("transmission", 100),),
        storage_delta=0,                          # 2000 == 1900 + 100
        actor="x",
    )
    assert is_conserved(e)


# ── 2. Emit signs only conserved events and the record verifies ──────────────

@pytest.mark.loom
def test_emit_signs_conserved_event_and_verifies():
    priv, _ = crypto.generate_keypair()
    loom = EnergyLoom(priv)
    e = _balanced(actor=loom.address)
    att = loom.emit(e)
    assert verify_record(att.record, att.author_pub, att.sig, "actor")
    assert att.record["kind"] == "energy-balance"
    assert att.record["total_generation_wh"] == 1000


@pytest.mark.loom
def test_emit_refuses_imbalanced_event():
    priv, _ = crypto.generate_keypair()
    loom = EnergyLoom(priv)
    bad = DispatchEvent(
        generation=(Flow("solar", 1000),),
        consumption=(Flow("town", 800),),         # 1000 != 800 (+0 loss/storage)
        losses=(),
        storage_delta=0,
        actor=loom.address,
    )
    with pytest.raises(ValueError, match="not conserved"):
        loom.emit(bad)


@pytest.mark.loom
def test_actor_must_match_signing_key():
    priv, _ = crypto.generate_keypair()
    loom = EnergyLoom(priv)
    with pytest.raises(ValueError, match="actor does not match"):
        loom.to_record(_balanced(actor="someone-else"))


# ── 3. Order-independent content addressing + Web weave ──────────────────────

@pytest.mark.loom
def test_flow_order_does_not_change_cid():
    priv, _ = crypto.generate_keypair()
    loom = EnergyLoom(priv)
    a = DispatchEvent(
        generation=(Flow("solar", 800), Flow("wind", 1200)),
        consumption=(Flow("town", 2000),),
        losses=(),
        storage_delta=0,
        actor=loom.address,
    )
    b = DispatchEvent(
        generation=(Flow("wind", 1200), Flow("solar", 800)),   # reversed
        consumption=(Flow("town", 2000),),
        losses=(),
        storage_delta=0,
        actor=loom.address,
    )
    assert canonical.cid(loom.to_record(a)) == canonical.cid(loom.to_record(b))


@pytest.mark.loom
def test_weave_into_web_returns_cid():
    priv, _ = crypto.generate_keypair()
    loom = EnergyLoom(priv)
    web = Web()
    cid, att = loom.weave(_balanced(actor=loom.address), web)
    assert isinstance(cid, str) and cid
    assert verify_record(att.record, att.author_pub, att.sig, "actor")


# ── 4. Validation guards (integer-only signed path) ──────────────────────────

@pytest.mark.loom
def test_flow_wh_must_be_positive_int():
    with pytest.raises(ValueError):
        Flow("solar", 0)
    with pytest.raises(ValueError):
        Flow("solar", -5)
    with pytest.raises(TypeError):
        Flow("solar", True)                       # bool must not pose as Wh


@pytest.mark.loom
def test_storage_delta_must_be_int_but_may_be_negative():
    # negative is allowed (discharge); bool is not
    DispatchEvent(generation=(Flow("g", 10),), consumption=(Flow("l", 20),),
                  losses=(), storage_delta=-10, actor="x")
    with pytest.raises(TypeError):
        DispatchEvent(generation=(Flow("g", 10),), consumption=(),
                      losses=(), storage_delta=True, actor="x")


@pytest.mark.loom
def test_empty_dispatch_rejected():
    with pytest.raises(ValueError):
        DispatchEvent(generation=(), consumption=(), losses=(), storage_delta=0, actor="x")
