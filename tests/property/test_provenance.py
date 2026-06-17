"""Proofs for provenance queries: full-depth ancestry, origins, and the DAG invariant."""

import pytest

from knitweb.fabric.provenance import ancestry, is_acyclic, origins, provenance
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


@pytest.mark.property
def test_full_depth_ancestry_reaches_the_origin():
    web, n = _chain()
    anc = ancestry(web, n["part"], rels={"derived-from"})
    # full chain back to the ore — NOT capped at Web.traverse's default depth
    assert set(anc) == {n["mach"], n["ingot"], n["smelt"], n["ore"]}
    assert n["part"] not in anc                       # start excluded


@pytest.mark.property
def test_provenance_attaches_records():
    web, n = _chain()
    prov = provenance(web, n["part"], rels={"derived-from"})
    assert prov["root"] == n["part"]
    assert set(prov["ancestors"]) == {n["mach"], n["ingot"], n["smelt"], n["ore"]}
    # every ancestor CID resolves to its woven record (the Web forbids dangling edges)
    assert prov["records"][n["ore"]]["sku"] == "IRON-ORE"
    assert prov["records"][n["smelt"]]["op"] == "smelting"
    assert all(rec is not None for rec in prov["records"].values())


@pytest.mark.property
def test_origins_are_the_raw_material_leaves():
    web, n = _chain()
    assert origins(web, n["part"], rels={"derived-from"}) == [n["ore"]]


@pytest.mark.property
def test_relation_filter_scopes_the_walk():
    web, n = _chain()
    # an unrelated edge type must not be followed when rels is restricted
    web.link(n["part"], n["ore"], "mentions")
    anc = ancestry(web, n["part"], rels={"derived-from"})
    assert n["ore"] in anc                            # via the derived-from chain only
    only_mentions = ancestry(web, n["part"], rels={"mentions"})
    assert only_mentions == [n["ore"]]                # one direct hop, nothing deeper


@pytest.mark.property
def test_provenance_must_be_acyclic():
    web, n = _chain()
    assert is_acyclic(web, n["part"], rels={"derived-from"})
    # introduce a cycle: ore "derived-from" the part -> no longer a DAG
    web.link(n["ore"], n["part"], "derived-from")
    assert not is_acyclic(web, n["part"], rels={"derived-from"})
    # ancestry stays bounded (terminates) even with the cycle present
    anc = ancestry(web, n["part"], rels={"derived-from"})
    assert n["ore"] in anc
