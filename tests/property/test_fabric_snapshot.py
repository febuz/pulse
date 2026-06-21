"""Proofs for the deterministic, read-only Web snapshot (the Lens export boundary)."""

import copy

import pytest

from knitweb.core import canonical
from knitweb.fabric.items import web_state_root
from knitweb.fabric.jsonld import export_web
from knitweb.fabric.snapshot import web_snapshot
from knitweb.fabric.web import Web


def _small_web():
    """A tiny float-free web with two nodes and one weighted edge."""
    web = Web()
    a = web.weave({"kind": "note", "text": "acid", "n": 1})
    b = web.weave({"kind": "note", "text": "base", "n": 2})
    web.link(a, b, "conjugate-of", weight=2)
    return web, a, b


@pytest.mark.property
def test_snapshot_reports_root_counts_records_and_export():
    web, a, b = _small_web()
    snap = web_snapshot(web)
    assert snap["state_root"] == web_state_root(web)
    assert snap["node_count"] == 2
    assert snap["edge_count"] == 1
    assert set(snap["records"]) == {a, b}
    assert snap["jsonld"] == export_web(web)


@pytest.mark.property
def test_snapshot_is_byte_stable_across_calls():
    web, _, _ = _small_web()
    first = web_snapshot(web)
    second = web_snapshot(web)
    assert first == second
    # stronger: identical canonical CBOR bytes (no insertion-order / identity leak)
    assert canonical.encode(first) == canonical.encode(second)


@pytest.mark.property
def test_snapshot_does_not_mutate_or_rewrite_the_web():
    web, _, _ = _small_web()
    root_before = web_state_root(web)
    size_before = web.size
    nodes_before = copy.deepcopy(web.nodes)
    web_snapshot(web)
    # no records/signatures/feeds rewritten, no edges woven
    assert web_state_root(web) == root_before
    assert web.size == size_before
    assert web.nodes == nodes_before


@pytest.mark.property
def test_snapshot_is_an_isolated_deep_copy():
    web, a, _ = _small_web()
    root_before = web_state_root(web)
    snap = web_snapshot(web)
    # mutating the snapshot must not reach back into live fabric state
    snap["records"][a]["text"] = "TAMPERED"
    snap["jsonld"]["@graph"].append({"@id": "spoofed"})
    assert web.nodes[a]["text"] == "acid"
    assert web_state_root(web) == root_before


@pytest.mark.property
def test_empty_web_snapshot_is_well_formed_and_stable():
    web = Web()
    snap = web_snapshot(web)
    assert snap["node_count"] == 0
    assert snap["edge_count"] == 0
    assert snap["records"] == {}
    assert snap["jsonld"]["@graph"] == []
    assert snap["state_root"] == web_state_root(web)
    assert canonical.encode(snap) == canonical.encode(web_snapshot(web))
