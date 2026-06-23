"""IL-108 — Bounded self-reflective iteration as the metered PoUW default.

Tests for three acceptance criteria:

AC1 — mode="reflect" is the metered default; mode="recurse" flagged unmetered/local-only
AC2 — metered path has hard step + token budget; budget_exhausted → best-so-far, never hangs
AC3 — benchmark harness: reflect vs recurse on fixed candidate set (reported honestly)
"""

from __future__ import annotations

import pytest

from knitweb.fabric.web import Web
from knitweb.interpret.distill import distill
from knitweb.interpret.retrieve import retrieve
from knitweb.pouw.job import (
    METERED_DISTILL_CONFIG,
    UNMETERED_RECURSE_FLAG,
    DistillJobConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fixed_web(n: int = 6) -> tuple[Web, str]:
    """Build a fixed n-node web for benchmark reproducibility. Returns (web, seed_cid)."""
    web = Web()
    first = web.weave({"kind": "knowledge", "title": "Seed", "scope": "public"})
    prev = first
    for i in range(1, n):
        node = web.weave({"kind": "knowledge", "title": f"Node{i}", "scope": "public"})
        web.link(prev, node, "supports", weight=1)
        prev = node
    return web, first


def _candidate_set(web: Web, seed_cid: str, *, subscription=("public",)):
    from knitweb.fabric.items import web_state_root
    wsc = web_state_root(web)
    return retrieve("Seed", subscription, web, web_state_cid=wsc)


# ---------------------------------------------------------------------------
# AC1 — mode defaults and metered/unmetered flag
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_distill_default_mode_is_reflect():
    """distill() signature defaults to mode='reflect'."""
    import inspect
    sig = inspect.signature(distill)
    assert sig.parameters["mode"].default == "reflect"


@pytest.mark.property
def test_metered_distill_config_uses_reflect():
    assert METERED_DISTILL_CONFIG.mode == "reflect"
    assert METERED_DISTILL_CONFIG.metered is True
    assert METERED_DISTILL_CONFIG.is_metered is True


@pytest.mark.property
def test_recurse_mode_is_unmetered_flag():
    assert UNMETERED_RECURSE_FLAG == "recurse"


@pytest.mark.property
def test_distill_job_config_recurse_requires_metered_false():
    """Creating a DistillJobConfig with recurse+metered=True must raise."""
    with pytest.raises(ValueError, match="unmetered"):
        DistillJobConfig(mode="recurse", metered=True)


@pytest.mark.property
def test_distill_job_config_recurse_metered_false_ok():
    cfg = DistillJobConfig(mode="recurse", metered=False)
    assert cfg.mode == "recurse"
    assert cfg.is_metered is False


@pytest.mark.property
def test_distill_job_config_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode"):
        DistillJobConfig(mode="hallucinate")


@pytest.mark.property
def test_distill_job_config_invalid_max_iters_raises():
    with pytest.raises(ValueError, match="max_iters"):
        DistillJobConfig(max_iters=0)


@pytest.mark.property
def test_metered_config_has_positive_budgets():
    assert METERED_DISTILL_CONFIG.max_iters >= 1
    assert METERED_DISTILL_CONFIG.max_tokens >= 1


# ---------------------------------------------------------------------------
# AC2 — hard step budget; budget_exhausted flag; never hangs
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_reflect_budget_exhausted_when_candidates_exceed_max_iters():
    """With max_iters=2 and 6 candidates, budget_exhausted must be True."""
    web, seed = _fixed_web(n=6)
    cands = _candidate_set(web, seed)
    if len(cands.cids) <= 2:
        pytest.skip("need more than 2 candidates")
    sel = distill(cands, "Seed", web=web, max_iters=2, mode="reflect")
    assert sel.log.budget_exhausted is True


@pytest.mark.property
def test_reflect_budget_not_exhausted_when_all_fit():
    """With max_iters=100 and 6 candidates, budget_exhausted should be False."""
    web, seed = _fixed_web(n=6)
    cands = _candidate_set(web, seed)
    sel = distill(cands, "Seed", web=web, max_iters=100, mode="reflect")
    assert sel.log.budget_exhausted is False


@pytest.mark.property
def test_reflect_returns_best_so_far_on_budget_exhaustion():
    """Budget exhaustion must return a valid Selection, not raise or hang."""
    web, seed = _fixed_web(n=8)
    cands = _candidate_set(web, seed)
    # Force exhaustion
    sel = distill(cands, "Seed", web=web, max_iters=1, mode="reflect")
    assert sel is not None
    assert isinstance(sel.relations, tuple)
    assert isinstance(sel.log.iterations, int)
    assert sel.log.iterations >= 1


@pytest.mark.property
def test_reflect_iterations_bounded_by_max_iters():
    """log.iterations must never exceed max_iters."""
    web, seed = _fixed_web(n=10)
    cands = _candidate_set(web, seed)
    for cap in (1, 3, 5):
        sel = distill(cands, "Seed", web=web, max_iters=cap, mode="reflect")
        assert sel.log.iterations <= cap, f"iterations {sel.log.iterations} exceeded cap {cap}"


@pytest.mark.property
def test_reflect_mode_does_not_multiply_max_iters():
    """mode='reflect' must NOT double max_iters (only recurse does)."""
    web, seed = _fixed_web(n=20)
    cands = _candidate_set(web, seed)
    cap = 3
    sel = distill(cands, "Seed", web=web, max_iters=cap, mode="reflect")
    assert sel.log.iterations <= cap


# ---------------------------------------------------------------------------
# AC3 — benchmark harness: reflect vs recurse on fixed candidate set
#        Reported honestly: recurse runs more iterations for the same input.
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_bench_recurse_runs_more_iters_than_reflect_on_same_input():
    """recurse mode doubles max_iters; it should process more candidates
    than reflect with the same max_iters cap on the same web."""
    web, seed = _fixed_web(n=10)
    cands = _candidate_set(web, seed)

    cap = 3
    reflect_sel = distill(cands, "Seed", web=web, max_iters=cap, mode="reflect")
    recurse_sel = distill(cands, "Seed", web=web, max_iters=cap, mode="recurse")

    # recurse doubles max_iters internally, so it always processes >= reflect
    assert recurse_sel.log.iterations >= reflect_sel.log.iterations, (
        f"expected recurse ({recurse_sel.log.iterations}) >= reflect ({reflect_sel.log.iterations})"
    )


@pytest.mark.property
def test_bench_reflect_is_bounded_recurse_is_not_for_pouw():
    """Document the policy: reflect is metered, recurse is not.

    For a fixed candidate set:
    - reflect with small cap is exhausted (budget_exhausted=True)
    - recurse with same cap processes more candidates (budget may not exhaust)
    """
    web, seed = _fixed_web(n=8)
    cands = _candidate_set(web, seed)
    if len(cands.cids) < 4:
        pytest.skip("need at least 4 candidates")

    cap = 2  # small cap: reflect is certainly exhausted, recurse (cap*2=4) may not be
    reflect_sel = distill(cands, "Seed", web=web, max_iters=cap, mode="reflect")
    recurse_sel = distill(cands, "Seed", web=web, max_iters=cap, mode="recurse")

    assert reflect_sel.log.budget_exhausted is True
    # recurse with doubled budget should reach more candidates or not exhaust
    assert (
        not recurse_sel.log.budget_exhausted
        or recurse_sel.log.iterations > reflect_sel.log.iterations
    )


@pytest.mark.property
def test_bench_reflect_sub_calls_bounded_by_max_iters():
    """reflect: sub_calls <= max_iters (each candidate processed at most once)."""
    web, seed = _fixed_web(n=10)
    cands = _candidate_set(web, seed)
    cap = 4
    sel = distill(cands, "Seed", web=web, max_iters=cap, mode="reflect")
    assert sel.log.sub_calls <= cap


@pytest.mark.property
def test_bench_metered_config_drives_bounded_distill():
    """METERED_DISTILL_CONFIG values yield a bounded, non-hanging distill run."""
    web, seed = _fixed_web(n=12)
    cands = _candidate_set(web, seed)
    cfg = METERED_DISTILL_CONFIG
    sel = distill(cands, "Seed", web=web, max_iters=cfg.max_iters, mode=cfg.mode)
    assert sel.log.iterations <= cfg.max_iters
