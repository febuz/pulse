"""IL-106 — Deterministic re-execution of retrieve + gate for distill PoUW jobs.

Tests for the two new additive surfaces:

A) pouw.sampling: should_audit_job / sample_distill_jobs (job-level audit selection)
B) pouw.verify: verify_distill (retrieve + gate re-execution verdict)
"""

from __future__ import annotations

import hashlib
from fractions import Fraction

import pytest

from knitweb.fabric.web import Web
from knitweb.interpret.distill import gate_relations
from knitweb.interpret.retrieve import retrieve
from knitweb.pouw.sampling import sample_distill_jobs, should_audit_job
from knitweb.pouw.verify import DistillReexecResult, verify_distill
from knitweb.synaptic.bytecode import Relation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEED = hashlib.sha256(b"test-epoch-0").digest()
_SEED2 = hashlib.sha256(b"test-epoch-1").digest()


def _two_node_web() -> tuple[Web, str, str]:
    web = Web()
    a = web.weave({"kind": "knowledge", "title": "Alpha", "scope": "public"})
    b = web.weave({"kind": "knowledge", "title": "Beta", "scope": "public"})
    web.link(a, b, "supports", weight=1)
    return web, a, b


def _retrieve_fn(query, subscription, web, *, web_state_cid=None):
    return retrieve(query, subscription, web, web_state_cid=web_state_cid)


def _gate_fn(relations, candidates, web):
    return gate_relations(relations, candidates, web)


# ---------------------------------------------------------------------------
# A) Job-level audit selection — should_audit_job / sample_distill_jobs
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_should_audit_job_rate_zero_never_audits():
    assert should_audit_job(_SEED, "bafy-abc", rate=Fraction(0)) is False


@pytest.mark.property
def test_should_audit_job_rate_one_always_audits():
    assert should_audit_job(_SEED, "bafy-abc", rate=Fraction(1)) is True


@pytest.mark.property
def test_should_audit_job_deterministic_same_inputs():
    r = Fraction(1, 2)
    result_a = should_audit_job(_SEED, "bafy-test-cid", rate=r)
    result_b = should_audit_job(_SEED, "bafy-test-cid", rate=r)
    assert result_a == result_b


@pytest.mark.property
def test_should_audit_job_different_seeds_can_differ():
    """Different epoch seeds should (in general) give different draws."""
    cid = "bafy-fixed-manifest"
    results = {should_audit_job(s, cid, rate=Fraction(1, 2))
               for s in [_SEED, _SEED2, b"seed3", b"seed4", b"seed5"]}
    assert len(results) == 2, "With 5 independent seeds, both True and False should appear"


@pytest.mark.property
def test_should_audit_job_rejects_float_rate():
    with pytest.raises(TypeError):
        should_audit_job(_SEED, "bafy-x", rate=0.1)  # type: ignore[arg-type]


@pytest.mark.property
def test_should_audit_job_rejects_non_bytes_seed():
    with pytest.raises(TypeError):
        should_audit_job("not-bytes", "bafy-x", rate=Fraction(1, 2))  # type: ignore[arg-type]


@pytest.mark.property
def test_should_audit_job_rejects_empty_manifest_cid():
    with pytest.raises(ValueError):
        should_audit_job(_SEED, "", rate=Fraction(1, 2))


@pytest.mark.property
def test_sample_distill_jobs_empty_list():
    assert sample_distill_jobs([], Fraction(1, 2), seed=_SEED) == []


@pytest.mark.property
def test_sample_distill_jobs_rate_zero_returns_empty():
    cids = ["bafy-a", "bafy-b", "bafy-c"]
    assert sample_distill_jobs(cids, Fraction(0), seed=_SEED) == []


@pytest.mark.property
def test_sample_distill_jobs_rate_one_returns_all():
    cids = ["bafy-a", "bafy-b", "bafy-c"]
    assert sample_distill_jobs(cids, Fraction(1), seed=_SEED) == cids


@pytest.mark.property
def test_sample_distill_jobs_deterministic():
    cids = [f"bafy-{i}" for i in range(20)]
    r1 = sample_distill_jobs(cids, Fraction(1, 3), seed=_SEED)
    r2 = sample_distill_jobs(cids, Fraction(1, 3), seed=_SEED)
    assert r1 == r2


@pytest.mark.property
def test_sample_distill_jobs_subset_of_input():
    cids = [f"bafy-{i}" for i in range(20)]
    selected = sample_distill_jobs(cids, Fraction(1, 2), seed=_SEED)
    assert all(c in cids for c in selected)


# ---------------------------------------------------------------------------
# B) verify_distill — re-execution of retrieve + gate
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_verify_distill_honest_bundle_passes():
    """A bundle with real, gated relations passes re-execution."""
    from knitweb.fabric.items import web_state_root
    web, a, b = _two_node_web()
    wsc = web_state_root(web)
    cand = _retrieve_fn("Alpha", ("public",), web, web_state_cid=wsc)
    # Build a plausible relation using CIDs present in candidates
    cids = list(cand.cids)
    if len(cids) < 2:
        pytest.skip("need at least 2 candidates to form a relation")
    rel = Relation(subject=cids[0], predicate=cids[1], obj=cids[0], source_type="Unknown", weight=1)

    class _Manifest:
        subscription = ("public",)
        web_state_cid = wsc
        query = "Alpha"

    # Gate the relation to confirm it's valid before testing the re-exec
    gated = gate_relations([rel], cand, web)
    if not gated:
        pytest.skip("relation did not pass gate (no attestation on test node)")

    result = verify_distill(
        _Manifest(),
        [gated[0]],
        web,
        retrieve_fn=_retrieve_fn,
        gate_fn=_gate_fn,
        original_query="Alpha",
    )
    assert result.deterministic_ok is True
    assert result.candidate_mismatch is False
    assert result.gate_failure is False
    assert result.first_bad_relation is None


@pytest.mark.property
def test_verify_distill_fabricated_cid_fails_candidate_check():
    """A relation with a CID not in the candidate set is caught as fabrication."""
    from knitweb.fabric.items import web_state_root
    web, a, b = _two_node_web()
    wsc = web_state_root(web)

    fabricated_cid = "bafyreifakenodecid000000000000000000000000000000000"
    rel = Relation(
        subject=fabricated_cid,
        predicate=a,
        obj=a,
        source_type="Unknown",
        weight=1,
    )

    class _Manifest:
        subscription = ("public",)
        web_state_cid = wsc
        query = "Alpha"

    result = verify_distill(
        _Manifest(),
        [rel],
        web,
        retrieve_fn=_retrieve_fn,
        gate_fn=_gate_fn,
        original_query="Alpha",
    )
    assert result.deterministic_ok is False
    assert result.candidate_mismatch is True
    assert result.first_bad_relation is rel


@pytest.mark.property
def test_verify_distill_empty_bundle_passes():
    """An empty bundle has no fabricated relations — re-exec passes trivially."""
    from knitweb.fabric.items import web_state_root
    web, a, b = _two_node_web()
    wsc = web_state_root(web)

    class _Manifest:
        subscription = ("public",)
        web_state_cid = wsc
        query = "Alpha"

    result = verify_distill(
        _Manifest(),
        [],
        web,
        retrieve_fn=_retrieve_fn,
        gate_fn=_gate_fn,
        original_query="Alpha",
    )
    assert result.deterministic_ok is True


@pytest.mark.property
def test_verify_distill_bad_web_state_cid_fails():
    """If retrieve raises (mismatched web_state_cid), result is deterministic_ok=False."""
    from knitweb.fabric.items import web_state_root
    web, a, b = _two_node_web()

    class _Manifest:
        subscription = ("public",)
        web_state_cid = "bafy-stale-state-000"   # wrong epoch
        query = "Alpha"

    result = verify_distill(
        _Manifest(),
        [],
        web,
        retrieve_fn=_retrieve_fn,
        gate_fn=_gate_fn,
        original_query="Alpha",
    )
    assert result.deterministic_ok is False
    assert result.candidate_mismatch is True


@pytest.mark.property
def test_verify_distill_result_fields():
    """DistillReexecResult exposes all expected boolean + optional fields."""
    r = DistillReexecResult(
        deterministic_ok=True,
        candidate_mismatch=False,
        gate_failure=False,
        first_bad_relation=None,
    )
    assert r.deterministic_ok is True
    assert r.candidate_mismatch is False
    assert r.gate_failure is False
    assert r.first_bad_relation is None
