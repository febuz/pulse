"""Tests for the interpretation lobe (retrieve/distill)."""

import random

import pytest

from knitweb.core import crypto
from knitweb.fabric.items import web_state_root
from knitweb.fabric.web import Web
from knitweb.interpret import Candidate, CandidateSet, distill, retrieve
from knitweb.interpret.quantize import quantize_weight


def _seeded_web(seed: int = 0) -> Web:
    rng = random.Random(seed)
    web = Web()
    cids = []

    # Keep scopes explicit so subscription filtering is testable.
    for i in range(8):
        cids.append(
            web.weave(
                {
                    "kind": "knowledge",
                    "title": f"item-{i}",
                    "body": f"body {i} with trace {rng.randint(0, 9999)}",
                    "scope": "public" if rng.random() < 0.7 else "secret",
                    "author": crypto.address(f"{rng.getrandbits(256):064x}"),
                }
            )
        )

    # Dense random links produce predictable deterministic neighbors.
    rel = ["supports", "observed-in", "depends-on"]
    for src in cids:
        for dst in rng.sample(cids, rng.randrange(0, 3)):
            if src != dst:
                web.link(src, dst, rel[rng.randrange(len(rel))], weight=1)
    return web


@pytest.mark.property
def test_retrieve_is_deterministic_for_the_same_inputs():
    web = _seeded_web(1)
    seed_node = next(iter(web.nodes))
    q = {"kind": "knowledge", "seed": seed_node}

    first = retrieve(q, ["public"], web)
    second = retrieve(q, ["public"], web)
    assert first.cids == second.cids
    assert first.web_state_cid == second.web_state_cid
    assert first.source_ancestries == second.source_ancestries


@pytest.mark.property
def test_retrieve_deterministic_for_many_webs():
    for seed in range(100):
        web = _seeded_web(seed)
        seed_node = next(iter(web.nodes))
        q = {"kind": "knowledge", "seed": seed_node}
        first = retrieve(q, ("public",), web)
        second = retrieve(q, ("public",), web)
        assert first.cids == second.cids


@pytest.mark.property
def test_subscription_filters_nodes_outside_scope():
    web = Web()
    public = web.weave(
        {
            "kind": "knowledge",
            "title": "public",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    secret = web.weave(
        {
            "kind": "knowledge",
            "title": "secret",
            "body": "sealed",
            "scope": "secret",
            "author": "alice",
        }
    )
    web.link(public, secret, "supports")

    result = retrieve(
        {"kind": "knowledge", "seed": public},
        subscription=("public",),
        web=web,
    )
    assert public in result.cids
    assert secret not in result.cids


@pytest.mark.property
def test_distill_gates_non_attested_nodes_out_of_web():
    web = Web()
    known = web.weave(
        {
            "kind": "knowledge",
            "title": "known",
            "body": "safe",
            "scope": "public",
            "author": "alice",
        }
    )
    bogus_cid = "ff" * 8
    candidate_set = CandidateSet(
        query="known",
        subscription=("public",),
        web_state_cid=web_state_root(web),
        cids=(known, bogus_cid),
        candidates=(Candidate(known, ()), Candidate(bogus_cid, ())),
        source_ancestries=((), ()),
    )

    selection = distill(candidate_set, "known", web=web, max_iters=10)
    assert len(selection.relations) == 1
    # relation from unknown candidate must be dropped by the provenance gate
    assert all(rel.subject == known for rel in selection.relations)


@pytest.mark.property
def test_distill_respects_iteration_cap_and_tracks_budget_flag():
    web = Web()
    cids = []
    for idx in range(20):
        cids.append(
            web.weave(
                {
                    "kind": "knowledge",
                    "title": f"item {idx}",
                    "body": "same",
                    "scope": "public",
                    "author": "alice",
                }
            )
        )
    for src in cids:
        web.link(src, cids[0], "supports")

    selection = distill(
        retrieve({"kind": "knowledge"}, ("public",), web, depth=0),
        cids[0],
        web=web,
        max_iters=3,
    )
    assert selection.log.iterations == 3
    assert selection.log.sub_calls == 3
    assert selection.log.cache_hits == 0
    assert selection.log.budget_exhausted is True
    assert len(selection.relations) <= 3


@pytest.mark.property
def test_distill_reuses_intermediate_nodes_for_identical_query():
    web = Web()
    node_a = web.weave(
        {
            "kind": "knowledge",
            "title": "A",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    node_b = web.weave(
        {
            "kind": "knowledge",
            "title": "B",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    web.link(node_a, node_b, "supports")

    first = distill(
        retrieve({"kind": "knowledge", "seed": node_a}, ("public",), web, depth=1),
        "test",
        web=web,
        max_iters=4,
    )
    size_after_first = len(web.nodes)

    second = distill(
        retrieve({"kind": "knowledge", "seed": node_a}, ("public",), web, depth=1),
        "test",
        web=web,
        max_iters=4,
    )
    assert len(web.nodes) == size_after_first
    assert second.log.cache_hits >= first.log.cache_hits
    assert len(second.intermediate_cids) == len(first.intermediate_cids)
    assert second.intermediate_cids == first.intermediate_cids


@pytest.mark.property
def test_distill_intermediate_links_to_relation_node():
    web = Web()
    node_a = web.weave(
        {
            "kind": "knowledge",
            "title": "A",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    node_b = web.weave(
        {
            "kind": "knowledge",
            "title": "B",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    web.link(node_a, node_b, "supports")

    selection = distill(
        retrieve({"kind": "knowledge", "seed": node_a}, ("public",), web, depth=1),
        {"subject": node_b, "predicate": "supports", "object": node_a},
        web=web,
        max_iters=3,
    )
    assert selection.intermediate_cids
    for intermediate in selection.intermediate_cids:
        assert any(e.rel == "distilled-from" for e in web._out.get(intermediate, ()))


def test_retrieve_respects_reputation_metadata_ordering():
    web = Web()
    a = web.weave(
        {
            "kind": "knowledge",
            "title": "A",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    high_rep = web.weave(
        {
            "kind": "knowledge",
            "title": "B",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    low_rep = web.weave(
        {
            "kind": "knowledge",
            "title": "C",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    web.link(a, high_rep, "supports", 1, metadata={"reputation": 9})
    web.link(a, low_rep, "supports", 1, metadata={"reputation": 2})

    result = retrieve({"seed": (high_rep, low_rep)}, ("public",), web, depth=0)
    assert result.cids == (high_rep, low_rep)


def test_quantize_weight_is_bound_and_deterministic():
    web = Web()
    a = web.weave(
        {
            "kind": "knowledge",
            "title": "A",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    b = web.weave(
        {
            "kind": "knowledge",
            "title": "B",
            "body": "open",
            "scope": "public",
            "author": "alice",
        }
    )
    web.link(a, b, "supports", 1, metadata={"reputation": 7})

    query = retrieve({"seed": (b,)}, ("public",), web, depth=0)
    selection = distill(
        query,
        {"pouw_score": 4, "recency": 1},
        web=web,
        max_iters=4,
    )

    assert len(selection.relations) == 1
    assert selection.relations[0].weight == quantize_weight(
        reputation=7,
        recency=1.0,
        pouw_score=4,
    )


def test_distill_query_fingerprint_handles_float_signal_inputs():
    web = Web()
    seed = web.weave(
        {"kind": "knowledge", "title": "A", "body": "open", "scope": "public", "author": "alice"}
    )
    target = web.weave(
        {"kind": "knowledge", "title": "B", "body": "open", "scope": "public", "author": "alice"}
    )
    web.link(seed, target, "supports")

    query = retrieve({"seed": (target,)}, ("public",), web, depth=0)
    selection = distill(
        query,
        {"pouw_score": 4.5, "recency": 0.75},
        web=web,
        max_iters=2,
    )
    assert len(selection.relations) == 1
