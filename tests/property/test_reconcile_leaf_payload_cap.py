"""A leaf is a *two-sided* exchange — its size shortcut must bound BOTH payloads.

``_on_probe`` stops bisecting and turns a range into a leaf when either side's
population is "small" (``<= LEAF_MAX``). But a leaf carries our raw CIDs and
provokes a *reply* leaf carrying the peer's — so the decision was keyed on the
wrong quantity. ``their_count <= LEAF_MAX`` would leaf the **whole keyspace** the
instant a peer is small, forcing the *other* side to dump its entire inventory in
one frame. When that inventory exceeds ``MAX_LEAF_CIDS`` (100k) ``build_leaf_frame``
raises ``ReconcileError`` — so an established node could never reconcile with a
fresh, near-empty peer (the ordinary bootstrap case).

The fix only takes the small-range shortcut when **both** payloads fit the hard
cap; otherwise it keeps bisecting until each leaf is bounded. These tests pin
that: a 100k-CID node converges with a 3-CID peer in *both* initiator roles, on
the *exact* symmetric difference, and no leaf frame ever exceeds the cap — while
the shortcut is still taken (no extra bisection) when both sides are genuinely
small. Pure integer logic: no clock, no rand, no canonical/wire/CID byte changes.
"""
from knitweb.p2p import reconcile as R
from knitweb.p2p.reconcile import (
    MAX_LEAF_CIDS,
    RECONCILE_LEAF,
    Reconciler,
    parse_leaf_frame,
    reconcile_pair,
    wire,
)

# Built once: an established node holding one CID past the single-frame cap, and a
# fresh peer with a handful (2 shared, 1 of its own). Pre-fix, the big side's leaf
# reply overflows the cap and reconcile_pair raises.
_BIG = [f"Qm{i:07d}" for i in range(MAX_LEAF_CIDS + 1)]
_SMALL = ["Qm0000000", "Qm0000001", "zzz_only_to_small"]
_BIG_SET = set(_BIG)
_SMALL_SET = set(_SMALL)


def _drive_capturing_max_leaf(a_cids, b_cids):
    """Drive a full pair exchange; return (largest leaf CID count, a.missing, b.missing)."""
    a = Reconciler(a_cids)
    b = Reconciler(b_cids)
    pending = a.open()
    receiver, sender = b, a
    biggest = 0
    for _ in range(10_000):
        if not pending:
            break
        replies = []
        for frame in pending:
            if wire.read_frame_bytes(frame).get("kind") == RECONCILE_LEAF:
                _, _, cids = parse_leaf_frame(frame)
                biggest = max(biggest, len(cids))
            replies.extend(receiver.on_frame(frame))
        pending = replies
        receiver, sender = sender, receiver
    else:  # pragma: no cover - defensive: the exchange must terminate
        raise AssertionError("exchange did not converge")
    return biggest, a.missing, b.missing


def test_huge_initiator_converges_with_tiny_peer_and_no_leaf_exceeds_the_cap():
    # Big node opens — its _on_leaf reply is what overflowed pre-fix. The instrumented
    # drive proves both: every leaf stays within the hard cap, AND each side converges
    # on the exact symmetric difference.
    biggest, a_missing, b_missing = _drive_capturing_max_leaf(_BIG, _SMALL)
    assert 0 < biggest <= MAX_LEAF_CIDS
    assert b_missing == _BIG_SET - _SMALL_SET     # tiny peer learns all it lacks
    assert a_missing == _SMALL_SET - _BIG_SET     # big node learns the 1 it lacks


def test_tiny_initiator_reconciles_with_huge_peer():
    # Tiny node opens; the huge side must bisect in _on_probe instead of leafing the
    # whole keyspace. reconcile_pair raises pre-fix; converging here proves the fix.
    res = reconcile_pair(_SMALL, _BIG)
    assert res["a_missing"] == _BIG_SET - _SMALL_SET
    assert res["b_missing"] == _SMALL_SET - _BIG_SET


def test_shortcut_still_taken_when_both_sides_are_small():
    # No-regression: when both populations fit the cap, the small-range shortcut is
    # unchanged — a mismatching small pair still leafs immediately rather than
    # over-bisecting. A single root probe -> one leaf reply (no child probes).
    a = Reconciler([f"x{i}" for i in range(5)])     # both well under LEAF_MAX
    b = Reconciler([f"x{i}" for i in range(3)])
    reply = b.on_frame(a.open()[0])
    assert len(reply) == 1
    assert wire.read_frame_bytes(reply[0]).get("kind") == RECONCILE_LEAF
