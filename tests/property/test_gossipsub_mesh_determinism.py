"""Mesh graft/prune selection is reproducible across processes — same seed, same mesh.

The module promises *"same seed -> same mesh"* and ``_select_graft`` claims its shuffle is
"deterministic via injected RNG". But the shuffled list was derived from a ``set``
(``_topic_peers - mesh`` for graft, ``list(mesh)`` for prune), and a ``set`` of strings
iterates in **PYTHONHASHSEED-randomised** order. ``random.shuffle`` permutes its input, so
its output depends on that initial order — meaning two processes with the same injected RNG
seed but different hash seeds picked *different* peers to graft/prune. The fix canonicalises
(``sorted``) the set-derived list before the shuffle, making selection a pure function of
``(seed, contents)``.

The cross-process test is the real proof; it spawns children under several hash seeds and
requires byte-identical selection. All integer/RNG, no wall-clock; touches no wire/CID path.

The unit selectors are not the whole story: the public ``heartbeat`` builds its topic
work-list with a bare ``set`` union (``set(self._mesh) | set(self._topic_peers)``) and
consumes the *single shared* ``_rng`` once per topic. So even with each topic's candidate
list canonicalised, hash-seed-randomised *topic* order makes two nodes draw the RNG in
different sequences and graft different peers. ``test_heartbeat_is_identical_across_hash_seeds``
pins that end-to-end — an in-process test cannot (set order is fixed within one process),
so it too must cross a process boundary.
"""
import os
import random
import subprocess
import sys

from knitweb.p2p.mesh import Gossipsub

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SRC = os.path.join(_REPO_ROOT, "src")

# Exercises BOTH set-derived selection paths (graft candidates + prune members).
_SELECT_SCRIPT = """
import random
from knitweb.p2p.mesh import Gossipsub
gs = Gossipsub(rng=random.Random(1234))
topic = "web/demo"
peers = ["peer-%03d" % i for i in range(64)]
for p in peers:
    gs.add_peer(topic, p)
graft = gs._select_graft(topic, set(), want=16)
prune = gs._select_prune(set(peers), drop=16)
print(",".join(graft) + "|" + ",".join(prune))
"""


# Exercises the PUBLIC heartbeat across MULTIPLE, overlapping topics so the shared RNG is
# drawn once per topic — the topic-iteration-order leak the unit script above cannot reach.
_HEARTBEAT_SCRIPT = """
import random
from knitweb.p2p.mesh import Gossipsub, parse_graft_frame
gs = Gossipsub(rng=random.Random(1234))
# Three topics with overlapping candidate pools: an empty mesh (degree 0 < d_low) grafts a
# 6-of-N subset per topic, so which peers are picked depends on the RNG draw *sequence*,
# which depends on topic order. Overlap means a peer can be grafted into >1 topic, so the
# per-peer frame list order also reflects topic order.
pools = {
    "a/x": ["p%03d" % i for i in range(0, 20)],
    "b/y": ["p%03d" % i for i in range(10, 30)],
    "c/z": ["p%03d" % i for i in range(20, 40)],
}
for topic, peers in pools.items():
    for p in peers:
        gs.add_peer(topic, p)
out = gs.heartbeat()  # topics=None -> the set-union work-list path under test
# Serialise sorted-by-peer (kills dict-order noise) but PRESERVE per-peer frame order
# (that order is the leak symptom). Topic membership differences show up here too.
parts = []
for peer in sorted(out):
    parts.append(peer + ":" + ",".join(parse_graft_frame(f) for f in out[peer]))
print("|".join(parts))
"""


def _gs():
    return Gossipsub(rng=random.Random(0))


def _run_under_hashseed(seed: str, script: str = _SELECT_SCRIPT) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": _SRC}
    out = subprocess.run(
        [sys.executable, "-c", script],
        env=env, cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_selection_is_identical_across_hash_seeds():
    # Same injected RNG seed, four different PYTHONHASHSEEDs -> identical selection.
    # Without the sorted() canonicalisation these diverge (set iteration order leaks).
    results = {h: _run_under_hashseed(h) for h in ("0", "1", "2", "12345")}
    distinct = set(results.values())
    assert len(distinct) == 1, f"selection diverged across hash seeds: {results}"
    # And it actually selected something (guards against a vacuous all-empty pass).
    graft, prune = next(iter(distinct)).split("|")
    assert len(graft.split(",")) == 16 and len(prune.split(",")) == 16


def test_heartbeat_is_identical_across_hash_seeds():
    # End-to-end: the PUBLIC heartbeat over multiple overlapping topics must produce the
    # identical graft plan under any PYTHONHASHSEED. Without sorting the topic work-list,
    # the shared RNG is drawn in hash-seed-dependent topic order -> divergent meshes, even
    # though each topic's candidate list is already canonical. This is the leak the unit
    # selectors miss.
    results = {h: _run_under_hashseed(h, _HEARTBEAT_SCRIPT) for h in ("0", "1", "2", "12345")}
    distinct = set(results.values())
    assert len(distinct) == 1, f"heartbeat diverged across hash seeds: {results}"
    # Non-vacuous: grafts actually happened across more than one topic, so cross-topic RNG
    # ordering was genuinely exercised (else the test would pass trivially).
    plan = next(iter(distinct))
    assert plan, "heartbeat produced no grafts — test would be vacuous"
    grafted_topics = {t for peer_entry in plan.split("|") for t in peer_entry.split(":", 1)[1].split(",") if t}
    assert len(grafted_topics) >= 2, f"only one topic grafted; cross-topic order not exercised: {plan}"


def test_eligible_candidates_returned_in_canonical_order():
    # Insertion order must not affect the candidate ordering handed to the RNG.
    names = [f"peer-{i:03d}" for i in range(64)]
    shuffled = names[:]
    random.Random(7).shuffle(shuffled)
    gs = _gs()
    for p in shuffled:
        gs.add_peer("t", p)
    assert gs._eligible_candidates("t", set()) == sorted(names)


def test_same_seed_same_mesh_in_process():
    # Two nodes, same injected seed, peers added in different orders -> same graft pick.
    names = [f"n{i:02d}" for i in range(40)]
    a, b = _gs(), _gs()
    for p in names:
        a.add_peer("t", p)
    for p in reversed(names):
        b.add_peer("t", p)
    assert a._select_graft("t", set(), want=8) == b._select_graft("t", set(), want=8)
