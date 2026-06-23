"""Stage-tagging contract and Mining→Settlement boundary crossing."""

from __future__ import annotations

import pytest

from knitweb.interpret.contract import (
    STAGES,
    ImmutableStageError,
    tag_stage,
)
from knitweb.interpret.settlement import (
    BANNED_FIELDS,
    CROSSING_FIELDS,
    BoundaryViolation,
    cross_boundary,
)


# ---------------------------------------------------------------------------
# contract.tag_stage
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_tag_stage_stamps_valid_stage():
    record = {"result_cid": "abc123"}
    tagged = tag_stage(record, "RETRIEVE")
    assert tagged["_stage"] == "RETRIEVE"
    assert tagged["result_cid"] == "abc123"
    # original not mutated
    assert "_stage" not in record


@pytest.mark.property
def test_tag_stage_immutable_once_set():
    record = {"result_cid": "abc"}
    tagged = tag_stage(record, "RETRIEVE")
    with pytest.raises(ImmutableStageError):
        tag_stage(tagged, "DISTILL")


@pytest.mark.property
def test_tag_stage_unknown_stage_raises():
    with pytest.raises(ValueError, match="unknown stage"):
        tag_stage({}, "NOT_A_STAGE")


@pytest.mark.property
def test_tag_stage_all_known_stages_accepted():
    for stage in STAGES:
        result = tag_stage({}, stage)
        assert result["_stage"] == stage


# ---------------------------------------------------------------------------
# settlement.cross_boundary
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_cross_boundary_passes_only_crossing_fields():
    payload = {
        "result_cid": "cid123",
        "provenance_chain": ["a", "b"],
        "verdict": "accept",
        "extra_field": "should be stripped",
    }
    out = cross_boundary(payload)
    assert set(out.keys()) == {"result_cid", "provenance_chain", "verdict"}
    assert "extra_field" not in out


@pytest.mark.property
def test_cross_boundary_raises_on_banned_field():
    for field in BANNED_FIELDS:
        payload = {"result_cid": "cid1", field: "bad_value"}
        with pytest.raises(BoundaryViolation, match=field):
            cross_boundary(payload)


@pytest.mark.property
def test_cross_boundary_empty_payload_returns_empty():
    assert cross_boundary({}) == {}


@pytest.mark.property
def test_cross_boundary_partial_crossing_fields():
    out = cross_boundary({"result_cid": "x"})
    assert out == {"result_cid": "x"}
