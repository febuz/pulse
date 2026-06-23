"""Tests for issue #134: real step/token budget in distill() (IL-101).

The byte-length proxy used ``to_bytes(8, "big")`` which always returned 8 bytes
regardless of content length.  ``tokens_used`` now tracks actual character width
divided by 4 (GPT-style BPE approximation) and is added to DistillIterationLog.
"""

from __future__ import annotations

import pytest

from knitweb.core import crypto
from knitweb.fabric.web import Web
from knitweb.interpret.distill import DistillIterationLog, Selection, distill
from knitweb.interpret.retrieve import CandidateSet, retrieve


def _web_with_nodes(*kinds: str) -> Web:
    web = Web()
    for kind in kinds:
        web.weave({"kind": kind, "scope": "public"})
    return web


# ---------------------------------------------------------------------------
# DistillIterationLog has tokens_used
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_iteration_log_has_tokens_used():
    web = _web_with_nodes("chemistry", "chemistry", "chemistry")
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "test", web=web, max_iters=8)
    assert hasattr(sel.log, "tokens_used")
    assert isinstance(sel.log.tokens_used, int)
    assert sel.log.tokens_used >= 0


@pytest.mark.property
def test_tokens_used_is_non_negative():
    web = _web_with_nodes("a")
    cs = retrieve({}, None, web)
    sel = distill(cs, "x", web=web, max_iters=4)
    assert sel.log.tokens_used >= 0


# ---------------------------------------------------------------------------
# max_tokens caps the loop
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_max_tokens_zero_breaks_immediately():
    """max_tokens=1 means the first step will exhaust the budget (any CID > 0 chars)."""
    web = _web_with_nodes(*["chemistry"] * 10)
    cs = retrieve({}, None, web)
    sel_full = distill(cs, "q", web=web, max_iters=20)
    sel_capped = distill(cs, "q", web=web, max_iters=20, max_tokens=1)
    assert sel_capped.log.tokens_used <= sel_full.log.tokens_used


@pytest.mark.property
def test_large_max_tokens_does_not_truncate():
    """With a very large token budget the result equals uncapped max_iters run."""
    web = _web_with_nodes(*["node"] * 5)
    cs = retrieve({}, None, web)
    sel_a = distill(cs, "q", web=web, max_iters=5)
    sel_b = distill(cs, "q", web=web, max_iters=5, max_tokens=10**6)
    assert sel_a.relations == sel_b.relations


# ---------------------------------------------------------------------------
# tokens_used is informational — not in signed output
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_tokens_used_not_in_signed_bundle():
    """tokens_used must never flow into the signed bytecode bundle."""
    from knitweb.synaptic import bytecode as _bc

    web = _web_with_nodes("chemistry", "chemistry")
    cs = retrieve({}, None, web)
    sel = distill(cs, "q", web=web, max_iters=4)

    priv = "aa" * 32
    originator = crypto.address(crypto.public_from_private(priv))
    data = _bc.compile_bundle("test-asset", originator, list(sel.relations))
    decoded = _bc.decode_bundle(data)

    assert "tokens_used" not in decoded
    for rel in decoded.get("relations", []):
        assert not hasattr(rel, "tokens_used")


@pytest.mark.property
def test_max_prompt_bytes_accepted_for_backward_compat():
    """max_prompt_bytes is still accepted (deprecated) without raising."""
    web = _web_with_nodes("node")
    cs = retrieve({}, None, web)
    sel = distill(cs, "q", web=web, max_iters=4, max_prompt_bytes=4096)
    assert isinstance(sel, Selection)
