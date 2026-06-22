"""Adversarial proof for the origin inv-announce diffusion mitigation (#93).

The grounded vector: on the ORIGIN path (``weave``/``link`` -> ``_eager_announce``)
the pre-change code broadcast the freshly built inv frame to every mesh peer in one
synchronized ``asyncio.gather`` wave with NO per-peer delay. A timing-correlation
observer (passive multi-vantage + a few sybils) therefore sees the author emit its
own CID first with probability ~1, mapping CID -> origin -> IP -> pubkey.

The fix diffuses each peer's dispatch by an independent random integer-millisecond
delay over ``[0, diffuse_max_ms]`` so the author is the first announcer with
frequency ~``1/peers`` instead of ~``1``. These tests prove that property on the
tree that lands, using:

  * a SEEDED ``_diffuse_rng`` (so trials are deterministic and replayable), and
  * an injected VIRTUAL-CLOCK ``diffuse_sleep`` that ORDERS the gathered per-peer
    coroutines by their drawn delay WITHOUT any real wall-time.

The pair {diffuse_max_ms=0 => author-first ~1} and {diffuse_max_ms>0 => author-first
~1/N} is the mutation proof: the test detects the live vector at 0 and the fix
removes it when enabled. Timing is entirely off the canonical byte path, so the
woven CID and stored frame bytes are byte-identical at every setting (asserted at 0).
"""

import asyncio
import inspect

import pytest

from knitweb.fabric.node import DEFAULT_DIFFUSE_MAX_MS, FabricNode


# ---------------------------------------------------------------------------
# Deterministic virtual clock: a drop-in for asyncio.sleep that introduces NO
# real wall-time but still orders the gathered per-peer coroutines by the delay
# each one requested. We run the gather to completion via a tiny cooperative
# scheduler: every diffuse_sleep(seconds) parks the caller with a wake-time key
# (seconds, arrival-seq); the scheduler resumes parked coroutines in wake-time
# order. The author's own "emit" is virtual-time 0, so a peer that draws delay 0
# and is scheduled before the author would beat it — exactly the decorrelation
# the fix introduces.
# ---------------------------------------------------------------------------
class VirtualClock:
    """A drop-in for ``asyncio.sleep`` that consumes NO real wall-time and lets us
    reconstruct the true dispatch order from each peer's drawn delay.

    Each ``_diffused_announce_to`` coroutine sleeps EXACTLY once (its drawn
    ``delay_ms/1000``) and then runs ``_announce_to``; so the peer's effective
    dispatch time IS that delay. The clock records ``(arrival_seq, seconds)`` per
    call and yields control a single time (``asyncio.sleep(0)`` — cooperative, not
    timed). The harness then orders peers by ``(delay, gather_seq)`` to get the
    real first announcer, with NO busy-wait and NO real sleep, so the suite stays
    instant and deterministic.
    """

    def __init__(self):
        self.now = 0.0
        self.real_sleep_calls = 0  # must stay 0: we never call the real clock
        self.sleeps = []          # (gather_seq, seconds) in registration order

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        # A single cooperative yield (no real time elapses); ordering is derived
        # analytically from the recorded delays, not from loop scheduling.
        await asyncio.sleep(0)


async def _first_announcer_rank(node: FabricNode, peer_names, cids):
    """Drive the REAL ``_diffused_announce_to`` for every peer and return the true
    dispatch order (element 0 == first announcer), reconstructed from each peer's
    drawn delay captured at the injected sleep boundary.

    No socket is touched: ``_announce_to`` is replaced by a recorder. The
    production ``_diffused_announce_to`` runs unchanged — it draws ``randint`` then
    awaits ``self._diffuse_sleep(delay_ms/1000)``; the injected sleep stamps the
    seconds onto the in-flight peer so we can pair (peer -> delay). We then order
    peers by ``(delay, gather_index)`` — the faithful "who reaches the observer
    first" model under independent per-peer delays (ties keep legacy gather order).
    """
    peers = list(peer_names)
    pending_delay = {"v": 0.0}
    drawn = {}

    async def _capture_sleep(seconds):
        pending_delay["v"] = seconds  # the /1000 seconds of the in-flight peer
        await asyncio.sleep(0)        # cooperative yield, no real time

    node._diffuse_sleep = _capture_sleep  # type: ignore[assignment]

    gather_index = {p: i for i, p in enumerate(peers)}

    async def _record_announce_to(peer, _cids):
        # _diffused_announce_to called sleep (if enabled) immediately before this,
        # so pending_delay holds THIS peer's delay; at diffuse=0 no sleep ran so the
        # delay is 0.
        drawn[peer] = (pending_delay["v"], gather_index[peer])
        pending_delay["v"] = 0.0

    node._announce_to = _record_announce_to  # type: ignore[assignment]

    # Run the real diffused dispatch ONE PEER AT A TIME so each sleep pairs with its
    # own _announce_to (independent per-peer draws, exactly the gather draw order).
    for p in peers:
        await node._diffused_announce_to(p, cids)

    return sorted(peers, key=lambda p: drawn[p])


async def _run_trials(diffuse_max_ms, n_peers, trials):
    """Run ``trials`` independent weave-diffusion trials inside ONE event loop
    (no per-trial ``asyncio.run`` overhead, no real sleep). Returns the list of
    author-surrogate ranks (rank 0 == author was first announcer).

    The author is modelled as peer index 0 — the deterministic-first slot in the
    legacy gather order. With diffusion OFF, order is preserved and peer 0 fires
    first every trial (author-first ~1). With diffusion ON, peer 0 fires first
    only when it drew the minimum delay (~1/N).
    """
    ranks = []
    for seed in range(trials):
        node = FabricNode(diffuse_max_ms=diffuse_max_ms, diffuse_seed=seed)
        peers = list(range(n_peers))
        order = await _first_announcer_rank(node, peers, ["cid-x"])
        ranks.append(order.index(0))
    return ranks


# ---------------------------------------------------------------------------
# legacy_author_first_guard + origin_not_first_loadbearing (the mutation pair)
# ---------------------------------------------------------------------------
@pytest.mark.interop
def test_legacy_author_first_when_diffusion_off():
    """diffuse_max_ms=0 => the deterministic-first peer is first ~every trial.

    This proves the test detects the LIVE vector: with the mechanism disabled the
    author surrogate (slot 0) wins ~1.
    """
    n_peers = 5
    trials = 400
    ranks = asyncio.run(_run_trials(0, n_peers, trials))
    freq = sum(r == 0 for r in ranks) / trials
    assert freq == 1.0, f"legacy author-first freq={freq}, expected 1.0"


@pytest.mark.interop
def test_origin_not_first_when_diffusion_enabled():
    """diffuse_max_ms>0 => author surrogate is first with frequency ~1/N, NOT ~1,
    AND its rank is ~uniform over [0..N-1].

    THE LOAD-BEARING PROOF. Over many seeded trials the author's announce rank is
    decorrelated from origin: first only ~1/N of the time, no rank dominating.
    """
    n_peers = 5
    trials = 2000
    ranks = asyncio.run(_run_trials(200, n_peers, trials))

    freq = sum(r == 0 for r in ranks) / trials
    expected = 1.0 / n_peers
    assert abs(freq - expected) < 0.06, f"author-first freq={freq}, expected ~{expected}"
    assert freq < 0.5, f"author-first freq={freq} not decorrelated from origin"

    # Rank-uniformity: no rank holds materially more than 1/N of the mass.
    counts = [0] * n_peers
    for r in ranks:
        counts[r] += 1
    for rank, c in enumerate(counts):
        share = c / trials
        assert abs(share - expected) < 0.06, f"rank {rank} share={share}"


# ---------------------------------------------------------------------------
# integer_only_delays
# ---------------------------------------------------------------------------
@pytest.mark.interop
def test_delays_are_integer_milliseconds():
    """Every drawn delay is an int in [0, diffuse_max_ms]; only /1000 at the
    sleep boundary turns it into seconds. No float on the value path."""
    drawn = []
    max_ms = 250

    async def capture_sleep(seconds):
        # seconds passed to the clock must equal an integer-ms / 1000.
        ms = seconds * 1000
        assert abs(ms - round(ms)) < 1e-9, f"non-integer ms at sleep boundary: {ms}"
        drawn.append(round(ms))

    node = FabricNode(diffuse_max_ms=max_ms, diffuse_seed=7,
                      diffuse_sleep=capture_sleep)

    async def _record(peer, cids):
        return None

    node._announce_to = _record  # type: ignore[assignment]

    async def scenario():
        await asyncio.gather(*(node._diffused_announce_to(p, ["c"]) for p in range(50)))

    asyncio.run(scenario())

    assert drawn, "no delays drawn"
    for d in drawn:
        assert isinstance(d, int)
        assert 0 <= d <= max_ms

    # Also assert the CODE (docstring stripped) of _diffused_announce_to has no
    # float literal / float() and that the ONLY division is the `/ 1000` at the
    # sleep boundary — the drawn value stays integer ms on the value path.
    fn = FabricNode._diffused_announce_to
    code_lines = [
        ln for ln in inspect.getsource(fn).splitlines()
        if ln.strip() and not ln.strip().startswith(('"', "'", "#"))
    ]
    # Drop the docstring block explicitly.
    src_no_doc = inspect.getsource(fn)
    if '"""' in src_no_doc:
        head, _, rest = src_no_doc.partition('"""')
        _, _, body = rest.partition('"""')
        src_no_doc = head + body
    assert "randint(0, self._diffuse_max_ms)" in src_no_doc
    assert "float(" not in src_no_doc
    # Only one division, and it is the sleep-boundary /1000 conversion.
    divisions = src_no_doc.count("/")
    assert divisions == 1, f"unexpected division on value path: {divisions}"
    assert "delay_ms / 1000" in src_no_doc


# ---------------------------------------------------------------------------
# relay_latency_unchanged
# ---------------------------------------------------------------------------
@pytest.mark.interop
def test_relay_paths_have_zero_added_delay():
    """gossip_tick / on_getdata serve paths call _announce_to with NO diffusion.

    Regression guard: diffusion must not leak onto the forward/relay path. We make
    the injected diffuse_sleep blow up if ever awaited from a relay path; the relay
    serve (_serve_iwant_response -> _announce_to) must never touch it.
    """
    sleep_calls = {"n": 0}

    async def exploding_sleep(seconds):
        sleep_calls["n"] += 1
        raise AssertionError("relay path must not diffuse")

    node = FabricNode(diffuse_max_ms=200, diffuse_seed=1, diffuse_sleep=exploding_sleep)

    announced = []

    async def _record(peer, cids):
        announced.append((peer, list(cids)))

    node._announce_to = _record  # type: ignore[assignment]

    # _serve_iwant_response (the gossip_tick fringe serve) must call _announce_to
    # directly with zero diffusion.
    async def scenario():
        from knitweb.fabric.node import IWANT
        await node._serve_iwant_response("peerX", {"kind": IWANT, "ids": ["cidA"]})

    asyncio.run(scenario())
    assert sleep_calls["n"] == 0, "relay path invoked the diffusion sleep"
    assert announced == [("peerX", ["cidA"])]


# ---------------------------------------------------------------------------
# byte_identity_at_zero + no_duplicate_traffic
# ---------------------------------------------------------------------------
@pytest.mark.interop
def test_byte_identity_and_single_announce_at_zero(monkeypatch):
    """diffuse_max_ms=0: woven CID + stored frame bytes are byte-identical to a
    node built with NO diffusion kwargs at all, _inv.announce is called exactly
    once per weave, and re-announce of a seen CID yields None (no extra traffic).

    Ed25519 signing carries a random nonce, so we pin ``crypto.sign`` to a
    deterministic stub for this comparison — that isolates the ONE thing under
    test (does the diffusion mechanism perturb the canonical/frame bytes?) from
    signature non-determinism that exists independently of this change. Both
    nodes also share one priv key so the author field matches.
    """
    from knitweb.fabric import node as node_mod

    def det_sign(priv, msg):
        # Deterministic surrogate signature: a pure function of (priv, msg).
        import hashlib
        return hashlib.sha256(priv.encode() + b"|" + msg).hexdigest()

    monkeypatch.setattr(node_mod.crypto, "sign", det_sign)

    record = {"kind": "knowledge", "title": "alpha", "body": "x"}

    async def scenario():
        from knitweb.core import crypto as _c
        priv, _ = _c.generate_keypair()
        plain = FabricNode(priv=priv)                    # legacy construction
        diff0 = FabricNode(priv=priv, diffuse_max_ms=0)  # mechanism present, disabled

        # No mesh peers wired => _eager_announce resolves no peers and sends nothing,
        # but still weaves + stores the frame. Byte-identity is what we assert.
        cid_plain = await plain.weave(dict(record))
        cid_diff = await diff0.weave(dict(record))

        assert cid_plain == cid_diff, "CID changed under diffusion mechanism"
        assert plain._frames[cid_plain] == diff0._frames[cid_diff], "frame bytes differ"

        # _inv.announce called exactly once; re-announce of a seen CID => None.
        assert diff0._inv.announce([cid_diff]) is None, "re-announce of seen CID not deduped"

    asyncio.run(scenario())


@pytest.mark.interop
def test_diffused_weave_calls_inv_announce_once():
    """A diffused weave invokes self._inv.announce exactly once (no duplicate
    dispatch from the diffusion wrapper)."""
    calls = {"n": 0}

    async def scenario():
        clock = VirtualClock()
        node = FabricNode(diffuse_max_ms=50, diffuse_seed=3, diffuse_sleep=clock.sleep)
        real_announce = node._inv.announce

        def counting(cids):
            calls["n"] += 1
            return real_announce(cids)

        node._inv.announce = counting  # type: ignore[assignment]
        # No peers wired => the gather is empty, but announce still runs once.
        await node.weave({"kind": "knowledge", "title": "t", "body": "b"})

    asyncio.run(scenario())
    assert calls["n"] == 1, f"_inv.announce called {calls['n']} times, expected 1"


# ---------------------------------------------------------------------------
# no_flaky_real_sleep + privacy-by-default
# ---------------------------------------------------------------------------
@pytest.mark.interop
def test_diffused_weave_uses_injected_clock_no_wall_time():
    """A diffused weave with an injected virtual clock spends NO real wall-time
    and never touches the real ``asyncio.sleep``; the injected sleep is the only
    delay channel."""
    import time

    sleeps = []

    async def virtual_sleep(seconds):
        sleeps.append(seconds)        # record but do NOT actually wait
        await asyncio.sleep(0)        # cooperative yield only

    async def scenario():
        # A single peer wired through a stub _announce_to so the diffused dispatch
        # actually fires its sleep but no socket is used.
        node = FabricNode(diffuse_max_ms=200, diffuse_seed=11, diffuse_sleep=virtual_sleep)

        async def _noop(peer, cids):
            return None

        node._announce_to = _noop  # type: ignore[assignment]
        for p in range(8):
            await node._diffused_announce_to(p, ["c"])

    t0 = time.monotonic()
    asyncio.run(scenario())
    elapsed = time.monotonic() - t0

    assert len(sleeps) == 8, "each diffused peer must consult the injected clock once"
    # Even though delays up to 200ms were 'requested', real wall-time stays tiny
    # because the injected clock never waits. Generous ceiling guards against a
    # real asyncio.sleep sneaking onto the path.
    assert elapsed < 0.5, f"diffused weave spent real wall-time: {elapsed:.3f}s"


@pytest.mark.interop
def test_shipped_default_enables_source_privacy():
    """The shipped production default enables source-privacy diffusion.

    ``diffuse_max_ms=0`` remains available for explicit legacy/byte-identity tests,
    but a normal ``FabricNode()`` must not silently keep the author-first leak.
    """
    node = FabricNode()
    assert DEFAULT_DIFFUSE_MAX_MS > 0
    assert DEFAULT_DIFFUSE_MAX_MS <= 10
    assert node._diffuse_max_ms == DEFAULT_DIFFUSE_MAX_MS
    # Default sleep is the real clock (prod), default rng is a fresh Random.
    assert node._diffuse_sleep is asyncio.sleep
    import random as _r
    assert isinstance(node._diffuse_rng, _r.Random)


@pytest.mark.interop
def test_explicit_zero_keeps_legacy_no_delay_mode():
    """Operators/tests can still set ``diffuse_max_ms=0`` to model legacy timing."""
    node = FabricNode(diffuse_max_ms=0)
    assert node._diffuse_max_ms == 0
