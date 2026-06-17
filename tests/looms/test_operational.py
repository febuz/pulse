"""Proofs for the operational loom: a provider can't sell capacity it doesn't have.

The gate is an inequality — total leased units must not exceed the resource capacity.
A sound allocation signs + verifies + is order-independent; an over-allocation is
refused before signing.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import verify_record
from knitweb.fabric.web import Web
from knitweb.looms.operational import (
    AllocationEvent,
    Lease,
    OperationalLoom,
    Resource,
    allocated_units,
    idle_units,
    is_within_capacity,
)


def _alloc(provider: str) -> AllocationEvent:
    gpu = Resource("gpu-3090", capacity=24)
    return AllocationEvent(
        resource=gpu,
        leases=(Lease("pls1aaa", 8, 5), Lease("pls1bbb", 10, 4)),
        provider=provider,
    )


@pytest.mark.loom
def test_within_capacity_accounting():
    e = _alloc("p")
    assert allocated_units(e) == 18
    assert idle_units(e) == 6
    assert is_within_capacity(e)


@pytest.mark.loom
def test_emit_signs_valid_allocation_and_verifies():
    priv, _ = crypto.generate_keypair()
    loom = OperationalLoom(priv)
    gpu = Resource("gpu-3090", 24)
    event = AllocationEvent(gpu, (Lease("pls1aaa", 8, 5), Lease("pls1bbb", 10, 4)), loom.address)
    att = loom.emit(event)
    assert att.record["allocated"] == 18 and att.record["idle"] == 6
    assert att.verify(author_field="provider")
    assert verify_record(att.record, att.author_pub, att.sig, "provider")
    assert canonical.decode(canonical.encode(att.record)) == att.record


@pytest.mark.loom
def test_full_allocation_is_allowed():
    priv, _ = crypto.generate_keypair()
    loom = OperationalLoom(priv)
    gpu = Resource("gpu-3090", 24)
    event = AllocationEvent(gpu, (Lease("pls1aaa", 24, 5),), loom.address)
    att = loom.emit(event)                          # exactly at capacity is fine
    assert att.record["idle"] == 0 and att.verify(author_field="provider")


@pytest.mark.loom
def test_over_allocation_is_refused():
    priv, _ = crypto.generate_keypair()
    loom = OperationalLoom(priv)
    gpu = Resource("gpu-3090", 24)
    oversold = AllocationEvent(gpu, (Lease("pls1aaa", 20, 5), Lease("pls1bbb", 10, 4)), loom.address)
    assert allocated_units(oversold) == 30 and not is_within_capacity(oversold)
    with pytest.raises(ValueError, match="over-allocation"):
        loom.emit(oversold)


@pytest.mark.loom
def test_lease_order_does_not_change_content_id():
    priv, _ = crypto.generate_keypair()
    loom = OperationalLoom(priv)
    gpu = Resource("gpu-3090", 24)
    e1 = AllocationEvent(gpu, (Lease("pls1aaa", 8, 5), Lease("pls1bbb", 10, 4)), loom.address)
    e2 = AllocationEvent(gpu, (Lease("pls1bbb", 10, 4), Lease("pls1aaa", 8, 5)), loom.address)
    assert loom.to_record(e1) == loom.to_record(e2)
    assert canonical.cid(loom.to_record(e1)) == canonical.cid(loom.to_record(e2))


@pytest.mark.loom
def test_weave_is_content_addressed_and_idempotent():
    priv, _ = crypto.generate_keypair()
    loom = OperationalLoom(priv)
    web = Web()
    cid, att = loom.weave(_alloc(loom.address), web)
    assert cid in web.nodes and web.nodes[cid] == att.record
    cid2, _ = loom.weave(_alloc(loom.address), web)
    assert cid2 == cid


@pytest.mark.loom
def test_tampered_capacity_fails_verification():
    priv, _ = crypto.generate_keypair()
    loom = OperationalLoom(priv)
    att = loom.emit(_alloc(loom.address))
    forged = dict(att.record, capacity=9999)        # inflate capacity post-signing
    assert not verify_record(forged, att.author_pub, att.sig, "provider")
