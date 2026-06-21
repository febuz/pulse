"""Proofs for the stable Lens provenance query contract.

Covers relation-filtered ancestry and origins, deterministic ordering (across repeated
calls, across different insertion orders, and for inputs the contract itself sorts but
upstream does not), dangling-reference (missing-node) visibility, and the degenerate
graph shapes a Lens can throw at the boundary — empty/unknown start, self-loops,
multi-origin diamonds, and cycles that must terminate.
"""

import pytest

from knitweb.fabric.provenance_contract import (
    ProvenanceQueryResult,
    provenance_query,
)
from knitweb.fabric.web import Web


def _chain():
    # ore (origin) -> smelting -> ingot -> machining -> part (product)
    # each derived record links to what it came from via "derived-from".
    web = Web()
    ore = web.weave({"kind": "material", "sku": "IRON-ORE"})
    smelt = web.weave({"kind": "process", "op": "smelting"})
    ingot = web.weave({"kind": "material", "sku": "IRON-INGOT"})
    mach = web.weave({"kind": "process", "op": "machining"})
    part = web.weave({"kind": "material", "sku": "GEAR"})
    web.link(smelt, ore, "derived-from")
    web.link(ingot, smelt, "derived-from")
    web.link(mach, ingot, "derived-from")
    web.link(part, mach, "derived-from")
    return web, dict(ore=ore, smelt=smelt, ingot=ingot, mach=mach, part=part)


def _diamond():
    # Two distinct raw-material origins reachable by two disjoint paths:
    #   part -> proc_a -> ore_a
    #   part -> proc_b -> ore_b
    # Origin ordering is non-trivial (two leaves), so a sort is observable.
    web = Web()
    ore_a = web.weave({"kind": "material", "sku": "ORE-A"})
    ore_b = web.weave({"kind": "material", "sku": "ORE-B"})
    proc_a = web.weave({"kind": "process", "op": "refine-a"})
    proc_b = web.weave({"kind": "process", "op": "refine-b"})
    part = web.weave({"kind": "material", "sku": "ALLOY"})
    web.link(part, proc_a, "derived-from")
    web.link(part, proc_b, "derived-from")
    web.link(proc_a, ore_a, "derived-from")
    web.link(proc_b, ore_b, "derived-from")
    return web, dict(
        ore_a=ore_a, ore_b=ore_b, proc_a=proc_a, proc_b=proc_b, part=part
    )


@pytest.mark.property
def test_relation_filtered_ancestry_present_to_full_depth():
    web, n = _chain()
    # A "mentions" node reachable ONLY via the mentions edge (NOT via the
    # derived-from chain). With the derived-from filter it must be ABSENT from
    # present; dropping the filter (rels=None) it WOULD appear. So this fixture
    # makes the relation filter load-bearing: rels=None vs {"derived-from"}
    # changes the checked result.
    note = web.weave({"kind": "note", "txt": "see also"})
    web.link(n["part"], note, "mentions")

    res = provenance_query(web, n["part"], rels={"derived-from"})
    assert isinstance(res, ProvenanceQueryResult)
    assert res.root == n["part"]
    assert res.rels == ("derived-from",)
    # full chain back to the ore, all present, start excluded, sorted
    assert set(res.present) == {n["mach"], n["ingot"], n["smelt"], n["ore"]}
    assert n["part"] not in res.present
    # the mentions-only node is filtered out — the derived-from rels cannot reach it
    assert note not in res.present
    assert res.missing == ()
    assert not res.has_dangling

    # Without the filter the mentions-only node WOULD appear: this is the
    # mutation guard — passing rels=None instead of {"derived-from"} changes
    # whether `note` is in present.
    res_all = provenance_query(web, n["part"], rels=None)
    assert res_all.rels is None
    assert note in res_all.present


@pytest.mark.property
def test_relation_filter_scopes_origins():
    web, n = _chain()
    # an unrelated "mentions" leaf must not be counted as a provenance origin
    extra = web.weave({"kind": "note", "txt": "see also"})
    web.link(n["part"], extra, "mentions")
    res = provenance_query(web, n["part"], rels={"derived-from"})
    assert res.origin_present == (n["ore"],)          # only the raw-material leaf
    assert extra not in res.present                   # mentions edge not followed
    # following every edge type does pull the mentions leaf in as an origin
    res_all = provenance_query(web, n["part"], rels=None)
    assert res_all.rels is None
    assert extra in res_all.origin_present


@pytest.mark.property
def test_deterministic_across_repeated_calls():
    web, n = _chain()
    a = provenance_query(web, n["part"], rels={"derived-from"})
    b = provenance_query(web, n["part"], rels={"derived-from"})
    assert a == b                                     # frozen dataclass value-equality
    # every reported list is sorted by CID
    for field in (a.present, a.missing, a.origin_present, a.origin_missing):
        assert list(field) == sorted(field)


@pytest.mark.property
def test_rels_sorted_by_contract_from_unsorted_input():
    # The contract performs its OWN sort of `rels` (tuple(sorted(rels))); the
    # upstream ancestry/origins walk only uses `rels` as a membership set, so it
    # never imposes this order. Pass an UNSORTED iterable of relation names and
    # require the result's `rels` to be the sorted tuple.
    #
    # Mutation criterion: if someone deleted the contract's tuple(sorted(rels)),
    # at least one of these unsorted inputs would surface in non-sorted order and
    # this test would fail.
    web, n = _chain()
    web.link(n["part"], n["ore"], "mentions")
    web.link(n["part"], n["mach"], "supports")

    unsorted_inputs = [
        {"supports", "mentions", "derived-from"},      # set: undefined iter order
        ["supports", "mentions", "derived-from"],       # descending list
        list(reversed(["derived-from", "mentions", "supports"])),
    ]
    expected = ("derived-from", "mentions", "supports")
    for rels in unsorted_inputs:
        res = provenance_query(web, n["part"], rels=rels)
        assert res.rels == expected
        assert list(res.rels) == sorted(res.rels)


@pytest.mark.property
def test_deterministic_across_insertion_orders():
    # Build the same DAG two ways: weave/link in different orders, same content.
    records = {
        "ore": {"kind": "material", "sku": "IRON-ORE"},
        "smelt": {"kind": "process", "op": "smelting"},
        "ingot": {"kind": "material", "sku": "IRON-INGOT"},
        "part": {"kind": "material", "sku": "GEAR"},
    }
    links = [("smelt", "ore"), ("ingot", "smelt"), ("part", "ingot")]

    def build(weave_order, link_order):
        web = Web()
        cids = {name: web.weave(records[name]) for name in weave_order}
        for src, dst in link_order:
            web.link(cids[src], cids[dst], "derived-from")
        return web, cids

    web1, c1 = build(["ore", "smelt", "ingot", "part"], links)
    web2, c2 = build(["part", "ingot", "smelt", "ore"], list(reversed(links)))
    assert c1 == c2                                   # CIDs are content-derived
    r1 = provenance_query(web1, c1["part"], rels={"derived-from"})
    r2 = provenance_query(web2, c2["part"], rels={"derived-from"})
    assert r1 == r2                                   # identical despite insertion order


@pytest.mark.property
def test_empty_unknown_start_yields_all_empty():
    # A start CID the Web has never seen has no ancestry and no origins; every
    # field is empty and nothing dangles.
    web, _ = _chain()
    res = provenance_query(web, "bafy-unknown-start-cid", rels={"derived-from"})
    assert res.present == ()
    assert res.missing == ()
    assert res.origin_present == ()
    assert res.origin_missing == ()
    assert res.has_dangling is False


@pytest.mark.property
def test_self_loop_excludes_start_from_its_own_present():
    # A node that links to itself must not appear in its own provenance: the
    # walk excludes the start CID even when an edge points back at it.
    web = Web()
    a = web.weave({"kind": "material", "sku": "SELF"})
    web.link(a, a, "derived-from")
    res = provenance_query(web, a, rels={"derived-from"})
    assert a not in res.present
    assert res.present == ()
    assert res.origin_present == ()
    assert res.has_dangling is False


@pytest.mark.property
def test_diamond_reports_exactly_two_sorted_origins():
    # A diamond has TWO distinct raw-material origins reachable by two paths.
    # Both must be reported, exactly once each, in sorted order — so origin
    # ordering is genuinely exercised (not a one-element list that is trivially
    # "sorted").
    web, n = _diamond()
    res = provenance_query(web, n["part"], rels={"derived-from"})
    assert set(res.origin_present) == {n["ore_a"], n["ore_b"]}
    assert len(res.origin_present) == 2
    assert list(res.origin_present) == sorted(res.origin_present)
    # both processing steps and both ores are present ancestors, start excluded
    assert set(res.present) == {
        n["proc_a"], n["proc_b"], n["ore_a"], n["ore_b"]
    }
    assert n["part"] not in res.present
    assert not res.has_dangling


@pytest.mark.property
def test_two_cycle_terminates_with_finite_result():
    # a <-> b is a 2-cycle. The walk must TERMINATE (no hang) and return a sane,
    # finite result: b is the single ancestor of a, start excluded, each once.
    web = Web()
    a = web.weave({"kind": "material", "sku": "A"})
    b = web.weave({"kind": "material", "sku": "B"})
    web.link(a, b, "derived-from")
    web.link(b, a, "derived-from")
    res = provenance_query(web, a, rels={"derived-from"})
    assert res.present == (b,)                         # finite, start excluded
    assert a not in res.present
    assert res.missing == ()
    assert not res.has_dangling


@pytest.mark.property
def test_missing_node_is_visible_not_dropped():
    web, n = _chain()
    # Drop a mid-chain ancestor's node record while its edges remain: a dangling
    # reference (e.g. a peer-fed edge whose target node has not synced yet).
    web.nodes.pop(n["ingot"])
    res = provenance_query(web, n["part"], rels={"derived-from"})
    # the dangling CID is surfaced in `missing`, not silently dropped
    assert n["ingot"] in res.missing
    assert n["ingot"] not in res.present
    assert res.has_dangling
    # present ancestors still resolve; the missing/present split is exhaustive
    assert set(res.present) == {n["mach"], n["smelt"], n["ore"]}
    assert set(res.present) | set(res.missing) == {
        n["mach"], n["ingot"], n["smelt"], n["ore"]
    }


@pytest.mark.property
def test_missing_leaf_reported_as_dangling_origin():
    web, n = _chain()
    # Drop the raw-material origin's record: its leaf reference must surface as a
    # missing origin, never be mistaken for a clean (present) root of the chain.
    web.nodes.pop(n["ore"])
    res = provenance_query(web, n["part"], rels={"derived-from"})
    assert res.origin_missing == (n["ore"],)
    assert n["ore"] not in res.origin_present
    assert n["ore"] in res.missing
