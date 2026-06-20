"""Regression guard for issue #134: wall-clock ``elapsed_ms`` byte-identity.

Sacred invariant: a wall-clock / non-deterministic value must NEVER feed any
signed or content-addressed output. ``elapsed_ms`` (computed from
``time.monotonic_ns`` in :mod:`knitweb.interpret.distill`) is informational only:
it is recorded on the :class:`DistillIterationLog` and must stay confined there.

If it ever leaked into the signed distill bundle, a committed web node payload,
a derived CID, or the ``web_state_root``, then two machines distilling the same
input would produce *different* signed bytes — a critical determinism breach.

These tests lock the confinement by running a representative distillation twice
with the monotonic clock monkeypatched to wildly different values (elapsed_ms 1
vs 999999) and asserting every signed / committed artefact is byte-identical.
"""

import importlib
import random

import pytest

from knitweb import sdk
from knitweb.core import crypto
from knitweb.fabric.items import web_state_root
from knitweb.fabric.web import Web
from knitweb.interpret import distill, retrieve

# The submodule object (not the re-exported ``distill`` function) so we can
# monkeypatch the ``time.monotonic_ns`` clock it reads.
distill_mod = importlib.import_module("knitweb.interpret.distill")


# A fixed private key keeps the asset id / originator stable across both runs so
# the only varying input is the monkeypatched clock.
_PRIV = "11" * 32


class _FakeClock:
    """A monotonic-ns stand-in: first call is ``start``, second is ``start+delta``.

    ``distill`` reads the clock exactly twice (start, end). By controlling the
    pair we pin ``elapsed_ms`` to an exact value per run.
    """

    def __init__(self, start: int, delta_ns: int) -> None:
        self._values = [start, start + delta_ns]
        self._i = 0

    def __call__(self) -> int:
        value = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return value


def _representative_web(seed: int = 7) -> Web:
    """A small but non-trivial attested web that yields gated relations."""
    rng = random.Random(seed)
    web = Web()
    cids = []
    for i in range(6):
        cids.append(
            web.weave(
                {
                    "kind": "knowledge",
                    "title": f"item-{i}",
                    "body": f"body {i}",
                    "scope": "public",
                    "author": "alice",
                }
            )
        )
    rels = ["supports", "observed-in", "depends-on"]
    for src in cids:
        for dst in rng.sample(cids, 2):
            if src != dst:
                web.link(src, dst, rels[rng.randrange(len(rels))], weight=1)
    return web


def _signed_run(monkeypatch, *, start: int, delta_ns: int):
    """Run the full read-path → signed-bundle pipeline under a pinned clock.

    Returns (bundle_bytes, bundle_digest, state_root_before, state_root_after,
    elapsed_ms).
    """
    web = _representative_web()
    state_root_before = web_state_root(web)

    monkeypatch.setattr(
        distill_mod.time, "monotonic_ns", _FakeClock(start, delta_ns)
    )

    # distill_bundle runs retrieve -> distill (records elapsed_ms) -> compile -> sign.
    # We also capture the Selection directly to inspect elapsed_ms + derived CIDs.
    candidates = retrieve("item-0", ("public",), web)
    # Re-pin the clock for the explicit distill call (consumed by the first run above).
    monkeypatch.setattr(
        distill_mod.time, "monotonic_ns", _FakeClock(start, delta_ns)
    )
    selection = distill(candidates, "item-0", web=web, max_iters=8)

    # Recompile the signed bundle from the selection's relations (the signed path).
    asset_id = "distill:fixed-asset-for-byte-identity-test"
    originator = crypto.address(crypto.public_from_private(_PRIV))
    from knitweb.synaptic import bytecode as _bc

    data = _bc.compile_bundle(asset_id, originator, list(selection.relations))
    digest = _bc.bundle_digest(data)
    state_root_after = web_state_root(web)
    return {
        "data": data,
        "digest": digest,
        "state_before": state_root_before,
        "state_after": state_root_after,
        "intermediate_cids": selection.intermediate_cids,
        "elapsed_ms": selection.log.elapsed_ms,
    }


@pytest.mark.property
def test_signed_bundle_byte_identical_regardless_of_elapsed_ms(monkeypatch):
    # Clock 1: tiny elapsed (1 ms). 1_000_000 ns == 1 ms.
    run1 = _signed_run(monkeypatch, start=0, delta_ns=1_000_000)
    # Clock 2: enormous elapsed (999999 ms).
    run2 = _signed_run(monkeypatch, start=10**12, delta_ns=999_999 * 1_000_000)

    # The two runs observed wildly different wall-clock elapsed values...
    assert run1["elapsed_ms"] == 1
    assert run2["elapsed_ms"] == 999_999
    assert run1["elapsed_ms"] != run2["elapsed_ms"]

    # ...yet every signed / committed artefact is byte-identical.
    assert run1["data"] == run2["data"], "signed bundle bytes diverged with elapsed_ms"
    assert run1["digest"] == run2["digest"], "bundle digest diverged with elapsed_ms"
    assert run1["state_after"] == run2["state_after"], "web_state_root diverged"
    assert run1["intermediate_cids"] == run2["intermediate_cids"], "derived CIDs diverged"

    # The post-distill web_state_root depends only on the woven structure, never
    # on the wall-clock: both runs land on the same Merkle root despite the clock.
    assert run1["state_after"] == run2["state_after"]


@pytest.mark.property
def test_distill_bundle_full_path_byte_identical_regardless_of_elapsed_ms(monkeypatch):
    """Exercise the public sdk.distill_bundle signed path end to end.

    The ECDSA signature itself is randomized (random k), so we assert byte
    identity on the *signed payload* (the bundle bytes that get signed) and that
    both signatures verify — the determinism contract is on the signed bytes.
    """
    def _run(start: int, delta_ns: int):
        web = _representative_web()
        monkeypatch.setattr(
            distill_mod.time, "monotonic_ns", _FakeClock(start, delta_ns)
        )
        data, sig = sdk.distill_bundle("item-0", ("public",), _PRIV, web=web)
        return data, sig

    data1, sig1 = _run(0, 1_000_000)
    data2, sig2 = _run(10**12, 999_999 * 1_000_000)

    assert data1 == data2, "sdk signed bundle bytes diverged with elapsed_ms"

    pub = crypto.public_from_private(_PRIV)
    assert sdk.verify_bundle(pub, data1, sig1)
    assert sdk.verify_bundle(pub, data2, sig2)
    # Signature over identical bytes verifies under either run's elapsed value.
    assert sdk.verify_bundle(pub, data2, sig1)


@pytest.mark.property
def test_elapsed_ms_only_lives_in_iteration_log_not_signed_structures():
    """Structural assertion: elapsed_ms is reachable only via the info log."""
    web = _representative_web()
    candidates = retrieve("item-0", ("public",), web)
    selection = distill(candidates, "item-0", web=web, max_iters=8)

    # elapsed_ms is on the informational log...
    assert isinstance(selection.log.elapsed_ms, int)
    assert hasattr(selection.log, "elapsed_ms")

    # ...and the decoded signed bundle carries no time field whatsoever.
    asset_id = "distill:struct-check"
    originator = crypto.address(crypto.public_from_private(_PRIV))
    from knitweb.synaptic import bytecode as _bc

    data = _bc.compile_bundle(asset_id, originator, list(selection.relations))
    decoded = _bc.decode_bundle(data)
    assert set(decoded.keys()) == {"asset_cid", "originator", "relations"}
    for rel in decoded["relations"]:
        # Relation fields are subject/predicate/obj/source_type/weight — no time.
        assert not hasattr(rel, "elapsed_ms")
        assert isinstance(rel.weight, int)
