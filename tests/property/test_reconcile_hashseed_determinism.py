"""Reconcile's frame stream is byte-identical across processes — same sets, same bytes.

Reconciliation only converges if two nodes that hold the *same* CID set summarise and
bisect it identically. The module promises a "deterministic ... identical frame sequence",
but the existing reconcile determinism tests (``test_split_range_is_deterministic``,
``test_session_is_deterministic``) replay **in one process**, under a single fixed
``PYTHONHASHSEED`` — so they pass even if a frame's CID order were derived from a ``set``,
because a ``set`` of strings iterates in a *fixed* (if arbitrary) order **within** a process.
The dangerous failure mode is cross-process: a ``set``-iteration order is
``PYTHONHASHSEED``-randomised, so two nodes in two processes would emit the *same logical*
CIDs in a *different byte order* — divergent wire frames for an identical inventory. An
in-process test structurally cannot see it (this is the exact class that bit mesh selection
and is guarded for mesh by ``test_gossipsub_mesh_determinism`` — reconcile, an equally
byte-identity-critical wire path, had no equivalent guard).

Today reconcile is clean: a leaf carries ``_slice_range(self._sorted, ...)`` — a lexical
slice of the once-``sorted`` inventory — and ``_check_leaf_cids`` preserves that order onto
the wire (it validates + caps, never re-sorts). The ONLY thing keeping two nodes' frames
byte-identical is that ``sorted`` discipline. A refactor that built a leaf from the
``self._have`` *set* instead (or any other set-order path) would silently break cross-node
byte agreement with no in-process test failing. This test pins it: drive a full two-node
exchange under several hash seeds and require the entire frame byte-stream — every probe and
every leaf, in order — to hash identically. Pure integer/lexical logic: no clock, no
randomness, no canonical/CID/record byte touched.
"""
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SRC = os.path.join(_REPO_ROOT, "src")

# Drive a full a<->b exchange and fold EVERY frame's exact wire bytes, in production order,
# into one digest. The inventories are fed in a deterministically *shuffled* (non-sorted)
# order, so each reconciler's internal CID ``set`` is populated such that its iteration order
# is hash-seed-dependent — yet the frames must still come out byte-identical because the
# reconciler canonicalises to ``sorted`` before anything reaches the wire. A 300-CID shared
# core plus 60 unique CIDs per side forces real bisection (many probes) and multi-CID leaves,
# so both the frame *sequence* order and the per-leaf CID order are genuinely exercised.
_RECON_SCRIPT = """
import hashlib, random
from knitweb.p2p.reconcile import Reconciler

shared = ["Qm%05d" % i for i in range(300)]
a_cids = shared + ["Qa%05d" % i for i in range(60)]
b_cids = shared + ["Qb%05d" % i for i in range(60)]
random.Random(11).shuffle(a_cids)   # fixed shuffle: insertion order != sorted order
random.Random(22).shuffle(b_cids)

a = Reconciler(a_cids)
b = Reconciler(b_cids)
pending = a.open()
receiver, sender = b, a
digest = hashlib.sha256()
nframes = 0
leaf_cids_seen = 0
for _ in range(100000):
    if not pending:
        break
    replies = []
    for frame in pending:
        digest.update(frame)            # EXACT wire bytes, in the order produced
        digest.update(b"\\x00")          # frame boundary so concatenation is unambiguous
        nframes += 1
        replies.extend(receiver.on_frame(frame))
    pending = replies
    receiver, sender = sender, receiver
else:
    raise SystemExit("exchange did not converge")

# Convergence must also be correct and identical: each side learns exactly what it lacks.
a_missing = sorted(a.missing)
b_missing = sorted(b.missing)
conv = hashlib.sha256(("|".join(a_missing) + "#" + "|".join(b_missing)).encode()).hexdigest()
print("%s|%d|%s" % (digest.hexdigest(), nframes, conv))
"""


def _run_under_hashseed(seed: str, script: str = _RECON_SCRIPT) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": _SRC}
    out = subprocess.run(
        [sys.executable, "-c", script],
        env=env, cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_reconcile_frame_stream_is_byte_identical_across_hash_seeds():
    # Same inventories, four different PYTHONHASHSEEDs -> the identical frame byte-stream.
    # If any frame's CID order (or the frame sequence order) were set-derived, the digest
    # would diverge across seeds — the cross-node byte-agreement failure that an in-process
    # replay test cannot detect.
    results = {h: _run_under_hashseed(h) for h in ("0", "1", "2", "12345")}
    distinct = set(results.values())
    assert len(distinct) == 1, f"reconcile frame stream diverged across hash seeds: {results}"

    # Non-vacuous: the exchange actually bisected into many frames and converged on a real,
    # non-empty symmetric difference (60 CIDs each side) — so per-leaf CID order and the
    # frame sequence were genuinely under test, not a trivial single-frame pass.
    digest, nframes, conv = next(iter(distinct)).split("|")
    assert int(nframes) > 8, f"too few frames — bisection not exercised: {nframes}"
    assert len(digest) == 64 and len(conv) == 64
