"""Property tests for Phase 2: fabric item types (knowledge, resource, checkpoint)."""

import pytest

from knitweb.core.pulse import Pulse
from knitweb.fabric.web import Web
from knitweb.fabric.items import (
    KnowledgeItem,
    ResourceItem,
    FabricCheckpoint,
    checkpoint,
    web_state_root,
)

_ADDR_A = "pls1aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_ADDR_B = "pls1bbbbbbbbbbbbbbbbbbbbbbbbbbbb"


# ---------------------------------------------------------------------------
# KnowledgeItem
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_knowledge_item_weave_and_cid():
    web = Web()
    item = KnowledgeItem(title="Fiber conservation", body="Mass is conserved.", author=_ADDR_A)
    woven_cid = item.weave(web)
    assert woven_cid == item.cid
    assert web.get(woven_cid) == item.to_record()


@pytest.mark.property
def test_knowledge_item_content_addressed():
    """Same logical content always produces the same CID."""
    a = KnowledgeItem(title="T", body="B", author=_ADDR_A, tags=("x", "y"))
    b = KnowledgeItem(title="T", body="B", author=_ADDR_A, tags=("y", "x"))
    assert a.cid == b.cid  # tags are sorted in to_record


@pytest.mark.property
def test_knowledge_item_weave_is_idempotent():
    web = Web()
    item = KnowledgeItem(title="T", body="B", author=_ADDR_A)
    cid_a = item.weave(web)
    cid_b = item.weave(web)
    assert cid_a == cid_b
    assert web.size[0] == 1


@pytest.mark.property
def test_knowledge_items_differ_on_different_content():
    a = KnowledgeItem(title="A", body=".", author=_ADDR_A)
    b = KnowledgeItem(title="B", body=".", author=_ADDR_A)
    assert a.cid != b.cid


@pytest.mark.property
def test_knowledge_item_can_be_linked():
    web = Web()
    ka = KnowledgeItem(title="Premise", body="P", author=_ADDR_A)
    kb = KnowledgeItem(title="Conclusion", body="C", author=_ADDR_A)
    cid_a = ka.weave(web)
    cid_b = kb.weave(web)
    edge = web.link(cid_a, cid_b, "supports")
    assert edge.src == cid_a and edge.dst == cid_b
    assert web.neighbors(cid_a, rel="supports") == [cid_b]


# ---------------------------------------------------------------------------
# ResourceItem
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_resource_item_weave_and_cid():
    web = Web()
    item = ResourceItem(
        resource_kind="gpu",
        capacity=4,
        price_per_epoch=1000,
        provider=_ADDR_B,
    )
    woven_cid = item.weave(web)
    assert woven_cid == item.cid
    assert web.get(woven_cid) == item.to_record()


@pytest.mark.property
def test_resource_item_content_addressed():
    a = ResourceItem(resource_kind="cpu", capacity=16, price_per_epoch=50, provider=_ADDR_A)
    b = ResourceItem(resource_kind="cpu", capacity=16, price_per_epoch=50, provider=_ADDR_A)
    assert a.cid == b.cid


@pytest.mark.property
def test_resource_item_rejects_negative_values():
    with pytest.raises(ValueError):
        ResourceItem(resource_kind="gpu", capacity=-1, price_per_epoch=10, provider=_ADDR_A)
    with pytest.raises(ValueError):
        ResourceItem(resource_kind="gpu", capacity=1, price_per_epoch=-1, provider=_ADDR_A)


@pytest.mark.property
def test_resource_item_rejects_bool_and_float_amounts():
    for bad in (True, 1.5):
        with pytest.raises(TypeError, match="capacity"):
            ResourceItem(resource_kind="gpu", capacity=bad, price_per_epoch=10, provider=_ADDR_A)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="price_per_epoch"):
            ResourceItem(resource_kind="gpu", capacity=1, price_per_epoch=bad, provider=_ADDR_A)  # type: ignore[arg-type]


@pytest.mark.property
def test_resource_item_different_providers_differ():
    a = ResourceItem(resource_kind="gpu", capacity=1, price_per_epoch=1, provider=_ADDR_A)
    b = ResourceItem(resource_kind="gpu", capacity=1, price_per_epoch=1, provider=_ADDR_B)
    assert a.cid != b.cid


@pytest.mark.property
def test_resource_item_no_float_in_record():
    item = ResourceItem(resource_kind="storage", capacity=1024, price_per_epoch=200, provider=_ADDR_A)
    rec = item.to_record()
    for v in rec.values():
        assert not isinstance(v, float), f"float found in record: {v}"


# ---------------------------------------------------------------------------
# web_state_root
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_web_state_root_empty():
    import hashlib
    web = Web()
    root = web_state_root(web)
    assert root == hashlib.sha256(b"").hexdigest()


@pytest.mark.property
def test_web_state_root_changes_on_new_item():
    web = Web()
    root_before = web_state_root(web)
    KnowledgeItem(title="New", body="item", author=_ADDR_A).weave(web)
    root_after = web_state_root(web)
    assert root_before != root_after


@pytest.mark.property
def test_web_state_root_is_order_independent():
    """Two Webs with the same nodes in different insertion order have the same root."""
    web1, web2 = Web(), Web()
    items = [
        KnowledgeItem(title="A", body=".", author=_ADDR_A),
        KnowledgeItem(title="B", body=".", author=_ADDR_A),
        KnowledgeItem(title="C", body=".", author=_ADDR_A),
    ]
    for item in items:
        item.weave(web1)
    for item in reversed(items):
        item.weave(web2)
    assert web_state_root(web1) == web_state_root(web2)


# ---------------------------------------------------------------------------
# FabricCheckpoint
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_checkpoint_is_anchored_to_beat():
    pulse = Pulse(interval_s=10, genesis_ts=0)
    web = Web()
    KnowledgeItem(title="First", body="fact", author=_ADDR_A).weave(web)

    beat = pulse.beat(10, state_root="placeholder")
    cp = checkpoint(web, beat)

    assert cp.epoch == beat.epoch
    assert cp.beat_cid == beat.cid
    assert cp.node_count == 1
    assert cp.edge_count == 0


@pytest.mark.property
def test_checkpoint_state_root_matches_web_state():
    pulse = Pulse(interval_s=10, genesis_ts=0)
    web = Web()
    KnowledgeItem(title="K", body="v", author=_ADDR_A).weave(web)
    beat = pulse.beat(0, state_root="root0")
    cp = checkpoint(web, beat)
    assert cp.state_root == web_state_root(web)


@pytest.mark.property
def test_checkpoint_cid_is_deterministic():
    pulse1, pulse2 = Pulse(10, 0), Pulse(10, 0)
    web1, web2 = Web(), Web()
    item = KnowledgeItem(title="Det", body="erministic", author=_ADDR_A)
    item.weave(web1)
    item.weave(web2)
    b1 = pulse1.beat(0, state_root="r")
    b2 = pulse2.beat(0, state_root="r")
    cp1 = checkpoint(web1, b1)
    cp2 = checkpoint(web2, b2)
    assert cp1.cid == cp2.cid


@pytest.mark.property
def test_checkpoint_woven_into_web():
    """A checkpoint can be woven into the Web to create auditable history."""
    pulse = Pulse(interval_s=10, genesis_ts=0)
    web = Web()
    ResourceItem(resource_kind="gpu", capacity=2, price_per_epoch=500, provider=_ADDR_B).weave(web)

    beat = pulse.beat(10, state_root="r1")
    cp = checkpoint(web, beat)
    cp_cid = cp.weave(web)

    assert cp_cid == cp.cid
    assert web.get(cp_cid) == cp.to_record()
    assert web.size[0] == 2  # resource item + checkpoint


@pytest.mark.property
def test_checkpoint_rejects_bool_and_float_counts():
    for field, bad in (
        ("epoch", True),
        ("epoch", 1.5),
        ("node_count", True),
        ("node_count", 1.5),
        ("edge_count", True),
        ("edge_count", 1.5),
    ):
        kwargs = {
            "epoch": 1,
            "beat_cid": "beat",
            "state_root": "root",
            "node_count": 1,
            "edge_count": 0,
        }
        kwargs[field] = bad
        with pytest.raises(TypeError, match=field):
            FabricCheckpoint(**kwargs)  # type: ignore[arg-type]


@pytest.mark.property
def test_sequential_checkpoints_track_growth():
    """Each Pulse beat captures a larger Web as more items are woven."""
    pulse = Pulse(interval_s=10, genesis_ts=0)
    web = Web()

    b0 = pulse.beat(0, state_root="r0")
    cp0 = checkpoint(web, b0)
    assert cp0.node_count == 0

    KnowledgeItem(title="A", body=".", author=_ADDR_A).weave(web)
    b1 = pulse.beat(10, state_root="r1")
    cp1 = checkpoint(web, b1)
    assert cp1.node_count == 1

    KnowledgeItem(title="B", body=".", author=_ADDR_A).weave(web)
    ResourceItem(resource_kind="cpu", capacity=8, price_per_epoch=100, provider=_ADDR_B).weave(web)
    b2 = pulse.beat(20, state_root="r2")
    cp2 = checkpoint(web, b2)
    assert cp2.node_count == 3

    assert cp0.state_root != cp1.state_root != cp2.state_root
