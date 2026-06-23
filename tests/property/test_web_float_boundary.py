"""P1-8: float type boundary in Web._validate_metadata_value.

ARCHITECTURE.md §5 R3: edge metadata values that reach canonical.encode must be
str, int, or bool — never float. canonical.encode already rejects floats with
CanonicalError, but the error should fire at the Web validation layer (earlier,
better message) rather than deep in the encoding path.
"""

import pytest

from knitweb.core import canonical
from knitweb.fabric.web import Web


# ── helpers ────────────────────────────────────────────────────────────────────

def _web_with_edge() -> tuple[Web, str, str]:
    """Return a Web with two nodes and a linked edge between them."""
    w = Web()
    cid_a = w.weave({"kind": "concept", "label": "A"})
    cid_b = w.weave({"kind": "concept", "label": "B"})
    w.link(cid_a, cid_b, "links", weight=1)
    return w, cid_a, cid_b


# ── float rejection tests ──────────────────────────────────────────────────────

def test_float_metadata_value_raises_type_error():
    """A float value in edge metadata must raise TypeError at the Web layer."""
    w, cid_a, cid_b = _web_with_edge()
    with pytest.raises(TypeError, match="float"):
        w.set_edge_metadata(cid_a, cid_b, "links", 1, {"score": 1.5})


def test_float_zero_also_rejected():
    """0.0 is still a float — must be rejected even though it equals int 0."""
    w, cid_a, cid_b = _web_with_edge()
    with pytest.raises(TypeError):
        w.set_edge_metadata(cid_a, cid_b, "links", 1, {"weight": 0.0})


def test_int_metadata_value_accepted():
    """Integers (incl. int(x * 1000) from quantize_weight) are accepted."""
    w, cid_a, cid_b = _web_with_edge()
    w.set_edge_metadata(cid_a, cid_b, "links", 1, {"score_milli": 750})
    from knitweb.fabric.web import Edge
    edge = next(e for e in w._out.get(cid_a, []) if e.rel == "links")
    assert w.edge_metadata(edge)["score_milli"] == 750


def test_str_metadata_value_accepted():
    w, cid_a, cid_b = _web_with_edge()
    w.set_edge_metadata(cid_a, cid_b, "links", 1, {"label": "strong"})


def test_bool_metadata_value_accepted():
    w, cid_a, cid_b = _web_with_edge()
    w.set_edge_metadata(cid_a, cid_b, "links", 1, {"verified": True})


def test_link_with_float_metadata_raises_immediately():
    """web.link(metadata=...) with a float value must raise before the edge lands."""
    w = Web()
    cid_a = w.weave({"kind": "concept", "label": "A"})
    cid_b = w.weave({"kind": "concept", "label": "B"})
    with pytest.raises(TypeError, match="float"):
        w.link(cid_a, cid_b, "links", metadata={"score": 0.95})


# ── canonical still rejects floats independently ───────────────────────────────

def test_canonical_still_rejects_float_directly():
    """canonical.encode raises CanonicalError for float — independent safety net."""
    with pytest.raises(canonical.CanonicalError, match="float"):
        canonical.encode({"value": 3.14})


# ── interpret → weave path: quantize_weight produces int, not float ────────────

def test_quantize_weight_output_is_int():
    """quantize_weight is the approved float→int conversion path."""
    from knitweb.interpret.quantize import quantize_weight
    result = quantize_weight(reputation=80, recency=0.75, pouw_score=0.9)
    assert isinstance(result, int), "quantize_weight must return int for canonical safety"

    w, cid_a, cid_b = _web_with_edge()
    # int from quantize_weight must be accepted as edge metadata
    w.set_edge_metadata(cid_a, cid_b, "links", 1, {"qw": result})
