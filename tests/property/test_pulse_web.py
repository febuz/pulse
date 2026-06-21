"""Property tests for the Pulse heartbeat and the Web woven graph (MVP modules)."""

import pytest

from knitweb.core import canonical
from knitweb.core.pulse import Beat, Pulse
from knitweb.fabric.web import Web


# --- Pulse ----------------------------------------------------------------

@pytest.mark.property
def test_pulse_epochs_and_chained_beats():
    pulse = Pulse(interval_s=10, genesis_ts=1000)
    assert pulse.epoch_at(1000) == 0
    assert pulse.epoch_at(1009) == 0
    assert pulse.epoch_at(1010) == 1
    assert pulse.epoch_at(1025) == 2

    b0 = pulse.beat(1000, state_root="00")
    b1 = pulse.beat(1010, state_root="11")
    b2 = pulse.beat(1020, state_root="22")
    assert (b0.epoch, b1.epoch, b2.epoch) == (0, 1, 2)
    assert b0.prev_beat is None
    assert b1.prev_beat == b0.cid
    assert b2.prev_beat == b1.cid
    assert pulse.verify_chain()


@pytest.mark.property
def test_pulse_rejects_non_advancing_epoch():
    pulse = Pulse(interval_s=10, genesis_ts=0)
    pulse.beat(15, state_root="aa")        # epoch 1
    with pytest.raises(ValueError):
        pulse.beat(12, state_root="bb")    # epoch 1 again -> no advance


@pytest.mark.property
def test_pulse_beat_cid_is_deterministic():
    p1 = Pulse(10, 0)
    p2 = Pulse(10, 0)
    assert p1.beat(0, "root").cid == p2.beat(0, "root").cid


@pytest.mark.property
def test_pulse_rejects_bool_and_float_timing_fields():
    for bad in (True, 1.5):
        with pytest.raises(TypeError, match="interval_s"):
            Pulse(bad, 0)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="genesis_ts"):
            Pulse(10, bad)  # type: ignore[arg-type]

    pulse = Pulse(10, 0)
    for bad in (True, 1.5):
        with pytest.raises(TypeError, match="timestamp"):
            pulse.epoch_at(bad)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="timestamp"):
            pulse.beat(bad, "root")  # type: ignore[arg-type]


@pytest.mark.property
def test_beat_rejects_bool_epoch_and_non_string_roots():
    with pytest.raises(TypeError, match="epoch"):
        Beat(epoch=True, timestamp=0, state_root="root", prev_beat=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="state_root"):
        Beat(epoch=0, timestamp=0, state_root=123, prev_beat=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="prev_beat"):
        Beat(epoch=0, timestamp=0, state_root="root", prev_beat=123)  # type: ignore[arg-type]


# --- Beat per-epoch mint cap (R1 follow-up: heartbeat-anchored supply governor) ---

@pytest.mark.property
def test_capless_beat_is_byte_identical_to_pre_cap_encoding():
    """A Beat with no cap must encode (and hash) byte-identically to the 4-field Beat.

    This is the byte-identity guard: ``epoch_mint_cap`` is conditionally OMITTED from
    to_record() when None, so adding the field cannot change any existing Beat's CID.
    """
    beat = Beat(epoch=3, timestamp=42, state_root="abcd", prev_beat=None)
    assert "epoch_mint_cap" not in beat.to_record()
    # Golden vector: the canonical CID of the literal pre-cap 4-field record.
    golden = canonical.cid(
        {
            "kind": "pulse-beat",
            "epoch": 3,
            "timestamp": 42,
            "state_root": "abcd",
            "prev_beat": None,
        }
    )
    assert beat.cid == golden
    # An explicit epoch_mint_cap=None is identical to omitting it entirely.
    assert Beat(3, 42, "abcd", None, epoch_mint_cap=None).cid == golden


@pytest.mark.property
def test_capped_beat_carries_cap_in_record_and_changes_cid():
    capless = Beat(epoch=0, timestamp=0, state_root="root", prev_beat=None)
    capped = Beat(epoch=0, timestamp=0, state_root="root", prev_beat=None, epoch_mint_cap=100)
    assert capped.to_record()["epoch_mint_cap"] == 100
    assert capped.cid != capless.cid           # the cap is consensus-visible in the CID
    # Determinism: same content -> same CID across instances.
    assert capped.cid == Beat(0, 0, "root", None, epoch_mint_cap=100).cid


@pytest.mark.property
def test_beat_rejects_bad_epoch_mint_cap():
    with pytest.raises(TypeError, match="epoch_mint_cap"):
        Beat(epoch=0, timestamp=0, state_root="root", prev_beat=None, epoch_mint_cap=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="epoch_mint_cap"):
        Beat(epoch=0, timestamp=0, state_root="root", prev_beat=None, epoch_mint_cap=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="epoch_mint_cap"):
        Beat(epoch=0, timestamp=0, state_root="root", prev_beat=None, epoch_mint_cap=-1)


@pytest.mark.property
def test_pulse_beat_propagates_cap_and_cap_for_epoch_reads_it():
    pulse = Pulse(interval_s=10, genesis_ts=0)
    b0 = pulse.beat(0, state_root="00", epoch_mint_cap=7)
    b1 = pulse.beat(10, state_root="11")                  # epoch 1, no cap
    assert b0.epoch_mint_cap == 7
    assert b1.epoch_mint_cap is None
    assert pulse.cap_for_epoch(0) == 7
    assert pulse.cap_for_epoch(1) is None                 # Beat present but capless
    assert pulse.cap_for_epoch(99) is None                # no Beat for the epoch
    assert pulse.verify_chain()                           # capped Beat still chains


# --- Web ------------------------------------------------------------------

@pytest.mark.property
def test_web_weave_is_idempotent_and_content_addressed():
    web = Web()
    rec = {"kind": "knowledge", "title": "fibers conserve mass"}
    cid_a = web.weave(rec)
    cid_b = web.weave({"title": "fibers conserve mass", "kind": "knowledge"})
    assert cid_a == cid_b                  # same content, one node
    assert web.size[0] == 1


@pytest.mark.property
def test_web_links_and_traversal():
    web = Web()
    a = web.weave({"n": "a"})
    b = web.weave({"n": "b"})
    c = web.weave({"n": "c"})
    web.link(a, b, "supports")
    web.link(b, c, "supports")
    web.link(a, c, "cites")

    assert set(web.neighbors(a)) == {b, c}
    assert web.neighbors(a, rel="supports") == [b]
    # 2 hops along any relation reaches b and c
    assert web.traverse(a, depth=2) == {b, c}
    # restrict to "supports": a->b->c
    assert web.traverse(a, depth=2, rels={"supports"}) == {b, c}
    # restrict to "cites" one hop: only c
    assert web.traverse(a, depth=1, rels={"cites"}) == {c}


@pytest.mark.property
def test_web_link_requires_known_nodes():
    web = Web()
    a = web.weave({"n": "a"})
    with pytest.raises(KeyError):
        web.link(a, "bogus-cid", "supports")
