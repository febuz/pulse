"""Proofs for the operational domain knitweb: only feasible allocations are signable.

An allocation that over-subscribes any resource (claimed > available capacity) must be
refused before signing. A feasible allocation becomes a signed, content-addressed,
order-independent record that weaves into the Web and verifies under the actor's key.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import verify_record
from knitweb.fabric.web import Web
from knitweb.knitwebs.operational import (
    AllocationEvent,
    Claim,
    OperationalKnitweb,
    Resource,
    capacity_balance,
    is_feasible,
)


def _gpu_pool() -> Resource:
    return Resource("gpu-pool", capacity=8)


def _cpu_pool() -> Resource:
    return Resource("cpu-pool", capacity=16)


def _feasible_event(actor: str) -> AllocationEvent:
    # Allocate 3 GPU slots to task-A and 5 to task-B (3+5=8, exactly at capacity)
    return AllocationEvent(
        resources=(_gpu_pool(),),
        claims=(
            Claim("gpu-pool", "task-A", 3),
            Claim("gpu-pool", "task-B", 5),
        ),
        actor=actor,
    )


@pytest.mark.knitweb
def test_feasible_allocation_passes_checks():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    event = _feasible_event(kw.address)
    bal = capacity_balance(event)
    assert bal == {"gpu-pool": 0}   # exactly at capacity
    assert is_feasible(event)


@pytest.mark.knitweb
def test_emit_signs_feasible_event_and_is_verifiable():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    event = _feasible_event(kw.address)
    att = kw.emit(event)
    assert att.record["feasible"] is True
    assert att.verify(author_field="actor")
    assert verify_record(att.record, att.author_pub, att.sig, "actor")
    # signed record round-trips through canonical CBOR
    assert canonical.decode(canonical.encode(att.record)) == att.record


@pytest.mark.knitweb
def test_over_subscribed_allocation_is_refused():
    # 5 + 5 = 10 > 8 capacity
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    bad = AllocationEvent(
        resources=(_gpu_pool(),),
        claims=(Claim("gpu-pool", "task-A", 5), Claim("gpu-pool", "task-B", 5)),
        actor=kw.address,
    )
    assert capacity_balance(bad) == {"gpu-pool": -2}
    assert not is_feasible(bad)
    with pytest.raises(ValueError, match="capacity exceeded"):
        kw.emit(bad)


@pytest.mark.knitweb
def test_multi_resource_feasible_allocation():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    event = AllocationEvent(
        resources=(_gpu_pool(), _cpu_pool()),
        claims=(
            Claim("gpu-pool", "model-inference", 4),
            Claim("cpu-pool", "data-prep", 12),
        ),
        actor=kw.address,
    )
    assert is_feasible(event)
    att = kw.emit(event)
    assert att.verify(author_field="actor")


@pytest.mark.knitweb
def test_multi_resource_partially_over_subscribed_is_refused():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    # cpu-pool is fine (12 <= 16) but gpu-pool is over (10 > 8)
    bad = AllocationEvent(
        resources=(_gpu_pool(), _cpu_pool()),
        claims=(
            Claim("gpu-pool", "job-1", 10),
            Claim("cpu-pool", "job-2", 12),
        ),
        actor=kw.address,
    )
    assert not is_feasible(bad)
    with pytest.raises(ValueError, match="capacity exceeded"):
        kw.emit(bad)


@pytest.mark.knitweb
def test_claim_order_does_not_change_content_id():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    e1 = AllocationEvent(
        resources=(_gpu_pool(),),
        claims=(Claim("gpu-pool", "task-A", 3), Claim("gpu-pool", "task-B", 5)),
        actor=kw.address,
    )
    e2 = AllocationEvent(
        resources=(_gpu_pool(),),
        claims=(Claim("gpu-pool", "task-B", 5), Claim("gpu-pool", "task-A", 3)),
        actor=kw.address,
    )
    assert kw.to_record(e1) == kw.to_record(e2)
    assert canonical.cid(kw.to_record(e1)) == canonical.cid(kw.to_record(e2))


@pytest.mark.knitweb
def test_duplicate_claim_units_do_not_change_content_id():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    e1 = AllocationEvent(
        resources=(_gpu_pool(),),
        claims=(Claim("gpu-pool", "task-A", 2), Claim("gpu-pool", "task-A", 1)),
        actor=kw.address,
    )
    e2 = AllocationEvent(
        resources=(_gpu_pool(),),
        claims=(Claim("gpu-pool", "task-A", 1), Claim("gpu-pool", "task-A", 2)),
        actor=kw.address,
    )
    assert kw.to_record(e1) == kw.to_record(e2)
    assert canonical.cid(kw.to_record(e1)) == canonical.cid(kw.to_record(e2))


@pytest.mark.knitweb
def test_weave_into_web_is_content_addressed_and_idempotent():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    web = Web()
    event = _feasible_event(kw.address)
    cid, att = kw.weave(event, web)
    assert cid in web.nodes
    assert web.nodes[cid] == att.record
    assert cid == canonical.cid(att.record)
    cid2, _ = kw.weave(event, web)
    assert cid2 == cid  # idempotent


@pytest.mark.knitweb
def test_tampered_signed_event_fails_verification():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    att = kw.emit(_feasible_event(kw.address))
    forged = dict(att.record, feasible=False)
    assert not verify_record(forged, att.author_pub, att.sig, "actor")


@pytest.mark.knitweb
def test_event_actor_must_match_signing_key():
    priv, _ = crypto.generate_keypair()
    other_priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    other = OperationalKnitweb(other_priv)
    event = _feasible_event(other.address)
    with pytest.raises(ValueError, match="actor"):
        kw.emit(event)


@pytest.mark.knitweb
def test_claim_references_undeclared_resource_is_rejected():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    with pytest.raises(ValueError, match="undeclared"):
        AllocationEvent(
            resources=(_gpu_pool(),),
            claims=(Claim("cpu-pool", "task-A", 1),),  # "cpu-pool" not in resources
            actor=kw.address,
        )


@pytest.mark.knitweb
def test_duplicate_resource_names_are_rejected():
    priv, _ = crypto.generate_keypair()
    kw = OperationalKnitweb(priv)
    with pytest.raises(ValueError, match="duplicate"):
        AllocationEvent(
            resources=(Resource("gpu-pool", 8), Resource("gpu-pool", 16)),
            claims=(Claim("gpu-pool", "task-A", 1),),
            actor=kw.address,
        )


@pytest.mark.knitweb
def test_zero_capacity_resource_is_rejected():
    with pytest.raises(ValueError, match="positive"):
        Resource("empty", capacity=0)


@pytest.mark.knitweb
def test_zero_unit_claim_is_rejected():
    with pytest.raises(ValueError, match="positive"):
        Claim("gpu-pool", "task-A", 0)


@pytest.mark.knitweb
def test_float_capacity_is_rejected():
    with pytest.raises(TypeError, match="int"):
        Resource("gpu-pool", capacity=4.5)  # type: ignore[arg-type]
