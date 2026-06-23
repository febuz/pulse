"""IL-103 — Distilled intermediates as content-addressed Web nodes.

Locks three properties of the distill() implementation:

1. Every intermediate is woven into the Web via Web.weave → CID-addressed.
2. Identical sub-query over identical candidate slice → cache hit (second run
   reuses the woven nodes, fewer sub_calls).
3. Intermediates link to their source relations with a ``distilled-from`` typed
   edge → provenance.ancestry reaches them.
"""

from __future__ import annotations

import pytest

from knitweb.fabric import provenance as prov
from knitweb.fabric.web import Web
from knitweb.interpret.distill import distill
from knitweb.interpret.retrieve import retrieve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DISTILLED_FROM = "distilled-from"


def _science_web() -> Web:
    """A small but realistic web: three knowledge nodes with edges."""
    web = Web()
    a = web.weave({"kind": "knowledge", "title": "item-A", "scope": "public"})
    b = web.weave({"kind": "knowledge", "title": "item-B", "scope": "public"})
    c = web.weave({"kind": "knowledge", "title": "item-C", "scope": "public"})
    web.link(a, b, "supports", weight=1)
    web.link(b, c, "supports", weight=1)
    return web


# ---------------------------------------------------------------------------
# AC 1 — intermediates are CID-addressed Web nodes
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_intermediate_cids_woven_into_web():
    """Every CID in Selection.intermediate_cids must be present in web.nodes."""
    web = _science_web()
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "item-A", web=web, max_iters=8)

    for icid in sel.intermediate_cids:
        assert icid in web.nodes, f"intermediate CID {icid!r} not woven into web"


@pytest.mark.property
def test_intermediate_cids_are_strings():
    web = _science_web()
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "item-A", web=web, max_iters=4)

    assert isinstance(sel.intermediate_cids, tuple)
    for icid in sel.intermediate_cids:
        assert isinstance(icid, str) and icid


@pytest.mark.property
def test_intermediate_records_are_dicts():
    """Woven intermediates are valid dict records, not None."""
    web = _science_web()
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "item-A", web=web, max_iters=4)

    for icid in sel.intermediate_cids:
        record = web.get(icid)
        assert isinstance(record, dict), f"intermediate {icid!r} record is not a dict"


# ---------------------------------------------------------------------------
# AC 2 — cache hits on repeated identical query
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_repeated_distill_yields_cache_hits():
    """Second distill() over same candidates on same web produces cache_hits > 0."""
    web = _science_web()
    cs = retrieve({}, ("public",), web)

    sel1 = distill(cs, "item-A", web=web, max_iters=8)
    # Run again on the same web (intermediates are already woven).
    sel2 = distill(cs, "item-A", web=web, max_iters=8)

    assert sel2.log.cache_hits > 0, (
        f"expected cache hits on second run; got cache_hits={sel2.log.cache_hits}"
    )


@pytest.mark.property
def test_cached_run_has_same_relations():
    """Cache hit must not change the selected relations — results are deterministic."""
    web = _science_web()
    cs = retrieve({}, ("public",), web)

    sel1 = distill(cs, "item-A", web=web, max_iters=8)
    sel2 = distill(cs, "item-A", web=web, max_iters=8)

    assert sel1.relations == sel2.relations


@pytest.mark.property
def test_cached_run_sub_calls_le_first():
    """Second run on warm cache must not make more sub_calls than the first."""
    web = _science_web()
    cs = retrieve({}, ("public",), web)

    sel1 = distill(cs, "item-A", web=web, max_iters=8)
    sel2 = distill(cs, "item-A", web=web, max_iters=8)

    assert sel2.log.sub_calls <= sel1.log.sub_calls, (
        f"second run sub_calls={sel2.log.sub_calls} > first={sel1.log.sub_calls}"
    )


# ---------------------------------------------------------------------------
# AC 3 — distilled-from typed edge + provenance.ancestry reachability
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_intermediates_have_distilled_from_edges():
    """Each intermediate CID must link to at least one relation via distilled-from."""
    web = _science_web()
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "item-A", web=web, max_iters=8)

    if not sel.intermediate_cids:
        pytest.skip("no intermediates produced (candidate set empty)")

    for icid in sel.intermediate_cids:
        linked = web.neighbors(icid, _DISTILLED_FROM)
        assert linked, (
            f"intermediate {icid!r} has no {_DISTILLED_FROM!r} edges in the Web"
        )


@pytest.mark.property
def test_distilled_from_targets_are_woven():
    """All nodes reachable via distilled-from from intermediates are in the Web."""
    web = _science_web()
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "item-A", web=web, max_iters=8)

    for icid in sel.intermediate_cids:
        for linked_cid in web.neighbors(icid, _DISTILLED_FROM):
            assert linked_cid in web.nodes, (
                f"distilled-from target {linked_cid!r} not woven into web"
            )


@pytest.mark.property
def test_provenance_ancestry_reaches_intermediates():
    """ancestry(web, intermediate, rels={'distilled-from'}) must find linked relations."""
    web = _science_web()
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "item-A", web=web, max_iters=8)

    if not sel.intermediate_cids:
        pytest.skip("no intermediates produced")

    found_any = False
    for icid in sel.intermediate_cids:
        ancestors = prov.ancestry(web, icid, rels={_DISTILLED_FROM})
        if ancestors:
            found_any = True
            # All ancestors must be present in the Web
            for acid in ancestors:
                assert acid in web.nodes

    assert found_any, "provenance.ancestry found no distilled-from ancestors"


# ---------------------------------------------------------------------------
# Idempotency — weave is content-addressed; same intermediate → same CID
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_intermediate_cids_deterministic_across_runs():
    """Two distill() calls with the same inputs and web produce the same intermediate CIDs."""
    web_a = _science_web()
    web_b = _science_web()

    cs_a = retrieve({}, ("public",), web_a)
    cs_b = retrieve({}, ("public",), web_b)

    sel_a = distill(cs_a, "item-A", web=web_a, max_iters=6)
    sel_b = distill(cs_b, "item-A", web=web_b, max_iters=6)

    assert sel_a.intermediate_cids == sel_b.intermediate_cids, (
        "intermediate CIDs are not deterministic across identical runs"
    )
