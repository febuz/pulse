"""Proofs for AttentionRecord — integer, CID-linked attention metadata for Lens."""

import pytest

from knitweb.core import canonical
from knitweb.fabric.items import AttentionRecord, KnowledgeItem
from knitweb.fabric.web import Web


def _target_cid():
    """A real node CID to annotate."""
    web = Web()
    target = web.weave(KnowledgeItem(title="acid", body="donates H+", author="pls1a").to_record())
    return web, target


@pytest.mark.property
def test_to_record_preserves_target_and_only_provided_metrics():
    _, target = _target_cid()
    rec = AttentionRecord(target=target, author="pls1lens", confidence=90, usefulness=75).to_record()
    assert rec["kind"] == "attention"
    assert rec["target"] == target          # linked CID preserved verbatim
    assert rec["confidence"] == 90
    assert rec["usefulness"] == 75
    # metrics never supplied are absent, not defaulted to zero
    assert "deploy_debug" not in rec
    assert "source_priority" not in rec
    assert "relation_weight" not in rec


@pytest.mark.property
@pytest.mark.parametrize("metric", AttentionRecord.__dataclass_fields__.keys() - {"target", "author"})
def test_float_and_bool_metrics_are_rejected(metric):
    _, target = _target_cid()
    base = {"target": target, "author": "pls1lens"}
    with pytest.raises(TypeError):
        AttentionRecord(**base, **{metric: 1.5})    # float rejected
    with pytest.raises(TypeError):
        AttentionRecord(**base, **{metric: True})   # bool rejected (bool is an int subclass)


@pytest.mark.property
def test_negative_metric_is_rejected():
    _, target = _target_cid()
    with pytest.raises(ValueError):
        AttentionRecord(target=target, author="pls1lens", source_priority=-1)


@pytest.mark.property
def test_linked_cid_survives_weave_and_read_and_cid_is_deterministic():
    web, target = _target_cid()
    rec = AttentionRecord(target=target, author="pls1lens", relation_weight=3)
    cid = rec.weave(web)
    stored = web.get(cid)
    assert stored["target"] == target               # link preserved through the Web
    assert rec.cid == cid == canonical.cid(rec.to_record())   # deterministic content id


@pytest.mark.property
def test_absent_metric_differs_from_zero_metric():
    _, target = _target_cid()
    absent = AttentionRecord(target=target, author="pls1lens")
    zero = AttentionRecord(target=target, author="pls1lens", confidence=0)
    assert "confidence" not in absent.to_record()
    assert zero.to_record()["confidence"] == 0
    # an asserted zero is a distinct signal from no assertion → distinct CIDs
    assert absent.cid != zero.cid


@pytest.mark.property
def test_identical_records_are_byte_stable():
    _, target = _target_cid()
    a = AttentionRecord(target=target, author="pls1lens", confidence=50, source_priority=2)
    b = AttentionRecord(target=target, author="pls1lens", confidence=50, source_priority=2)
    assert canonical.encode(a.to_record()) == canonical.encode(b.to_record())
