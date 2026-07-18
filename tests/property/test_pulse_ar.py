"""Proofs for Pulse AR: canonical observations, the bitchat BLE mesh, and the
YOLO→CNN→LLM glass loop (verify-before-trust, spatial filtering, determinism)."""

import pytest

from knitweb.core import canonical, crypto
from knitweb.edge.pulse_ar import (
    BitchatFrame,
    Detection,
    MeshNode,
    ObjectObservation,
    PriorsLLM,
    PulseARGlass,
    SignedObservation,
    StubYOLODetector,
    TaxonomyCNN,
    VisionPipeline,
    fragment,
)
from knitweb.fabric import spatial


# --- fixtures -------------------------------------------------------------

# Amsterdam; a peer ~40 m away shares a coarse cell, Paris does not.
_AMS = (52.3702, 4.8952)
_NEAR = (52.3705, 4.8955)
_PARIS = (48.8566, 2.3522)


def _pipeline():
    """A deterministic YOLO→CNN→LLM stack over a tiny office scene."""
    detector = StubYOLODetector({
        "chair": (10, 20, 100, 200),
        "laptop": (300, 120, 80, 60),
    })
    cnn = TaxonomyCNN({
        "chair": ("office_chair", "otkg:furniture/chair"),
        "laptop": ("laptop", "otkg:device/laptop"),
    })
    llm = PriorsLLM({
        "otkg:furniture/chair": {
            "width_mm": 600, "height_mm": 1100, "depth_mm": 620,
            "maker": "pls1maker_hermanmiller", "fiber_cid": "bcid-chair-fiber",
        },
        "otkg:device/laptop": {"width_mm": 304, "height_mm": 16, "depth_mm": 212},
    })
    return VisionPipeline(detector, cnn, llm)


def _glass(lat, lon, *, mesh=None, owner=""):
    priv, pub = crypto.generate_keypair()
    # precision 5 (~5 km "near me" cell), as in the arglass proximity proof: two
    # wearers ~40 m apart can straddle a finer cell boundary.
    return PulseARGlass(priv=priv, pub=pub, lat=lat, lon=lon,
                        pipeline=_pipeline(), precision=5, mesh=mesh)


# --- observation record ---------------------------------------------------

@pytest.mark.property
def test_observation_record_is_float_free_and_content_addressed():
    obs = ObjectObservation(
        label="office_chair", taxonomy="otkg:furniture/chair", confidence_bps=9500,
        geohash="u173zm", device="pls1device", owner="pls1owner", maker="pls1maker",
        width_mm=600, height_mm=1100, depth_mm=620, bbox=(10, 20, 100, 200),
    )
    # round-trips through canonical CBOR (no floats anywhere near the hash)
    assert canonical.decode(obs.canonical_bytes()) == obs.to_record()
    # deterministic id, identical for an identical observation
    twin = ObjectObservation.from_record(obs.to_record())
    assert twin == obs and twin.cid == obs.cid


@pytest.mark.property
def test_observation_rejects_floats_and_bad_confidence():
    with pytest.raises(TypeError):
        ObjectObservation(label="x", taxonomy="x", confidence_bps=1.0,  # float
                          geohash="u1", device="d")
    with pytest.raises(ValueError):
        ObjectObservation(label="x", taxonomy="x", confidence_bps=10001,  # > full
                          geohash="u1", device="d")
    with pytest.raises(ValueError):
        ObjectObservation(label="x", taxonomy="x", confidence_bps=100,
                          geohash="u1", device="d", width_mm=-5)  # negative mm


# --- signing / verify-before-trust ---------------------------------------

@pytest.mark.property
def test_signed_observation_binds_signature_to_device():
    priv, pub = crypto.generate_keypair()
    device = crypto.address(pub)
    obs = ObjectObservation(label="laptop", taxonomy="otkg:device/laptop",
                            confidence_bps=8800, geohash="u173zm", device=device)
    signed = SignedObservation.sign(obs, priv, pub)
    assert signed.verify()
    # a wire round-trip preserves verifiability
    assert SignedObservation.from_wire(signed.to_wire()).verify()


@pytest.mark.property
def test_cannot_sign_for_a_device_you_do_not_own():
    priv, pub = crypto.generate_keypair()
    obs = ObjectObservation(label="laptop", taxonomy="t", confidence_bps=8800,
                            geohash="u1", device="pls1someone_else")
    with pytest.raises(ValueError):
        SignedObservation.sign(obs, priv, pub)


@pytest.mark.property
def test_tampered_or_relabelled_observation_is_refused():
    priv, pub = crypto.generate_keypair()
    device = crypto.address(pub)
    obs = ObjectObservation(label="laptop", taxonomy="t", confidence_bps=8800,
                            geohash="u173zm", device=device)
    signed = SignedObservation.sign(obs, priv, pub)

    # flip a field but keep the old signature -> verification fails
    forged = SignedObservation(
        observation=ObjectObservation.from_record({**obs.to_record(),
                                                   "who": {"owner": "pls1thief", "maker": ""}}),
        pubkey=pub, signature=signed.signature,
    )
    assert not forged.verify()

    # a different key signing this device's claim (impersonation) -> fails
    other_priv, other_pub = crypto.generate_keypair()
    impostor = SignedObservation(observation=obs, pubkey=other_pub,
                                 signature=crypto.sign(other_priv, obs.canonical_bytes()))
    assert not impostor.verify()


# --- vision pipeline ------------------------------------------------------

@pytest.mark.property
def test_pipeline_couples_yolo_cnn_llm_into_observations():
    obs = _pipeline().observe(b"a photo of a chair and a laptop",
                              device="pls1dev", geohash="u173zmABC")
    by_label = {o.label: o for o in obs}
    assert set(by_label) == {"office_chair", "laptop"}
    chair = by_label["office_chair"]
    # CNN refined the class + taxonomy; LLM attached HOW (mm) + WHO (maker)
    assert chair.taxonomy == "otkg:furniture/chair"
    assert (chair.width_mm, chair.height_mm, chair.depth_mm) == (600, 1100, 620)
    assert chair.maker == "pls1maker_hermanmiller"
    assert chair.bbox == (10, 20, 100, 200)
    # CNN's second pass only raises confidence
    assert chair.confidence_bps >= 6000


@pytest.mark.property
def test_pipeline_is_deterministic():
    frame = b"a photo of a chair and a laptop"
    a = _pipeline().observe(frame, device="pls1dev", geohash="u173zm")
    b = _pipeline().observe(frame, device="pls1dev", geohash="u173zm")
    assert [o.cid for o in a] == [o.cid for o in b]


# --- bitchat mesh: fragmentation + reassembly ----------------------------

@pytest.mark.property
def test_fragmentation_round_trips_over_small_mtu():
    payload = bytes(range(256)) * 4      # 1 KiB, far bigger than one BLE write
    frames = fragment(payload, origin="pls1dev", ttl=7, mtu=180)
    assert len(frames) > 1 and all(len(f.chunk) <= 180 for f in frames)
    # frames survive a byte-level wire round-trip
    wire = [BitchatFrame.from_bytes(f.to_bytes()) for f in frames]
    assert b"".join(f.chunk for f in sorted(wire, key=lambda f: f.index)) == payload


@pytest.mark.property
def test_mesh_delivers_multihop_and_ttl_bounds_reach():
    # linear chain a - b - c - d - e
    nodes = [MeshNode(f"n{i}") for i in range(5)]
    for x, y in zip(nodes, nodes[1:]):
        x.connect(y)
    got: dict[str, list[bytes]] = {n.device_id: [] for n in nodes}
    for n in nodes:
        n.on_message(lambda p, o, dev=n.device_id: got[dev].append(p))

    nodes[0].publish(b"hello mesh", ttl=2)   # reach exactly 2 hops from the origin
    assert got["n1"] == [b"hello mesh"]       # 1 hop
    assert got["n2"] == [b"hello mesh"]       # 2 hops
    assert got["n3"] == [] and got["n4"] == []  # beyond ttl


@pytest.mark.property
def test_mesh_dedup_prevents_storm_in_a_cycle():
    # triangle: every node linked to every other -> a naive flood would loop forever
    a, b, c = MeshNode("a"), MeshNode("b"), MeshNode("c")
    a.connect(b); b.connect(c); a.connect(c)
    seen = {"a": 0, "b": 0, "c": 0}
    for n in (a, b, c):
        n.on_message(lambda p, o, dev=n.device_id: seen.__setitem__(dev, seen[dev] + 1))
    a.publish(b"once", ttl=7)
    # each non-origin node delivers the message exactly once despite the cycle
    assert seen == {"a": 0, "b": 1, "c": 1}


# --- full glass loop ------------------------------------------------------

@pytest.mark.property
def test_two_glasses_share_verified_observations_over_the_mesh():
    wearer = _glass(*_AMS)
    peer = _glass(*_NEAR)
    wearer.mesh.connect(peer.mesh)

    shared = wearer.observe_and_share(b"a photo of a chair and a laptop",
                                      observed_at=1, owner="pls1owner")
    assert len(shared) == 2 and all(s.verify() for s in shared)

    # the peer verified + kept both (they're in the same coarse cell)
    assert peer.observation_count == 2
    labels = {o["what"] for o in peer.overlays()}
    assert labels == {"office_chair", "laptop"}
    # overlays carry the full WHAT/WHO/WHERE/HOW/DEVICE answer
    chair = next(o for o in peer.overlays() if o["what"] == "office_chair")
    assert chair["dimensions_mm"] == (600, 1100, 620)
    assert chair["maker"] == "pls1maker_hermanmiller"
    assert chair["device"] == wearer.device


@pytest.mark.property
def test_glass_drops_far_observations_and_refuses_forged():
    wearer = _glass(*_AMS)
    far = _glass(*_PARIS)
    wearer.mesh.connect(far.mesh)

    # Paris peer publishes; the Amsterdam wearer verifies OK but drops on distance.
    far.observe_and_share(b"a photo of a chair")
    assert wearer.observation_count == 0

    # a forged envelope (valid-looking bytes, bad signature) is refused
    priv, pub = crypto.generate_keypair()
    obs = ObjectObservation(label="ghost", taxonomy="t", confidence_bps=9000,
                            geohash=wearer._anchor, device=crypto.address(pub))
    bad = SignedObservation(observation=obs, pubkey=pub, signature="00" * 70)
    assert wearer._ingest(bad.to_wire(), origin=obs.device) is False
    assert wearer.observation_count == 0


@pytest.mark.property
def test_features_are_deterministic_for_the_inner_model():
    wearer = _glass(*_AMS)
    peer = _glass(*_NEAR)
    wearer.mesh.connect(peer.mesh)
    wearer.observe_and_share(b"a photo of a chair and a laptop")

    feats = peer.features()
    assert feats == peer.features()                     # stable
    assert feats["office_chair"]["count"] == 1
    assert feats["office_chair"]["makers"] == ["pls1maker_hermanmiller"]
    assert feats["office_chair"]["taxonomies"] == ["otkg:furniture/chair"]
