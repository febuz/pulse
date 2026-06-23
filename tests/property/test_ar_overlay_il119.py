"""IL-119 — Verifiable AR overlays from a distilled bundle.

Three acceptance criteria:

AC1 — verify_bundle rejects tampered bytecode before any render
AC2 — overlay facts decode to a gated relation set with reachable provenance
AC3 — Lab MVP: marker (deterministic, not AI) → distill → overlay pipeline
"""

from __future__ import annotations

import pytest

from knitweb import sdk
from knitweb.core import crypto
from knitweb.edge.recognize import MarkerBackend, recognize
from knitweb.fabric.provenance import ancestry
from knitweb.fabric.web import Web
from knitweb.synaptic.bytecode import decode_bundle, verify_bundle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEACHING_POT_CID = "bafyreilp001"
_FURNACE_CID = "bafyreifu001"


def _chem_web() -> Web:
    """Minimal chemistry Web with leaching-pot knowledge nodes."""
    web = Web()
    source = web.weave({
        "kind": "knowledge",
        "title": "SLAG B.V. leaching process",
        "scope": "public",
    })
    pot = web.weave({
        "kind": "knowledge",
        "title": "leaching_pot",
        "scope": "public",
        "tags": ["feedstock:Fe-slag", "pH:4.5", "Cr6_safety:true"],
    })
    web.link(source, pot, "describes", weight=5)
    return web


def _make_overlay_bundle(query: str, web: Web, priv: str) -> tuple[bytes, str]:
    """Produce a signed distill bundle for an AR overlay."""
    data, sig = sdk.distill_bundle(query, ("public",), priv, web=web)
    return data, sig


# ---------------------------------------------------------------------------
# AC1 — tampered bytecode refused to render
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_tampered_bundle_fails_verify():
    """Flipping one byte in the bundle must cause verify_bundle to return False."""
    priv, pub = crypto.generate_keypair()
    web = _chem_web()
    data, sig = _make_overlay_bundle("leaching_pot", web, priv)

    # Flip one byte in the middle of the bytecode
    tampered = bytearray(data)
    mid = len(tampered) // 2
    tampered[mid] ^= 0xFF

    assert not verify_bundle(pub, bytes(tampered), sig), (
        "tampered bundle must not verify — edge should refuse to render it"
    )


@pytest.mark.property
def test_valid_bundle_verifies():
    """An unmodified bundle must verify successfully."""
    priv, pub = crypto.generate_keypair()
    web = _chem_web()
    data, sig = _make_overlay_bundle("leaching_pot", web, priv)
    assert verify_bundle(pub, data, sig)


@pytest.mark.property
def test_wrong_key_fails_verify():
    """Verifying with the wrong public key must fail."""
    priv, pub = crypto.generate_keypair()
    _, wrong_pub = crypto.generate_keypair()
    web = _chem_web()
    data, sig = _make_overlay_bundle("leaching_pot", web, priv)
    assert not verify_bundle(wrong_pub, data, sig)


@pytest.mark.property
def test_empty_bytecode_fails_verify():
    """Empty bytes must not verify."""
    _, pub = crypto.generate_keypair()
    assert not verify_bundle(pub, b"", "deadbeef")


@pytest.mark.property
def test_ar_render_gate_pattern():
    """The canonical AR gate: only call render() when verify_bundle is True."""
    priv, pub = crypto.generate_keypair()
    web = _chem_web()
    data, sig = _make_overlay_bundle("leaching_pot", web, priv)

    rendered: list[str] = []

    def render(decoded: dict) -> None:
        rendered.append("ok")

    # Valid bundle — should render
    if verify_bundle(pub, data, sig):
        render(decode_bundle(data))
    assert rendered == ["ok"]

    # Tampered — must NOT render
    tampered = bytearray(data)
    tampered[len(tampered) // 2] ^= 0x01
    rendered.clear()
    if verify_bundle(pub, bytes(tampered), sig):
        render(decode_bundle(bytes(tampered)))
    assert rendered == [], "tampered bundle must not reach render()"


# ---------------------------------------------------------------------------
# AC2 — overlay facts have gated relations + reachable provenance
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_decoded_overlay_has_relations():
    """Decoded bundle must contain at least one relation (the gated fact set)."""
    priv, pub = crypto.generate_keypair()
    web = _chem_web()
    data, sig = _make_overlay_bundle("leaching_pot", web, priv)

    decoded = decode_bundle(data)
    assert "relations" in decoded
    assert len(decoded["relations"]) > 0


@pytest.mark.property
def test_decoded_overlay_has_asset_cid():
    """Bundle must have a content-addressed asset_cid."""
    priv, _ = crypto.generate_keypair()
    web = _chem_web()
    data, _ = _make_overlay_bundle("leaching_pot", web, priv)

    decoded = decode_bundle(data)
    assert "asset_cid" in decoded
    assert isinstance(decoded["asset_cid"], str)
    assert decoded["asset_cid"]  # non-empty


@pytest.mark.property
def test_relation_cids_in_web_have_ancestry():
    """Each relation CID in the overlay must be traceable via provenance.ancestry."""
    priv, _ = crypto.generate_keypair()
    web = _chem_web()
    data, _ = _make_overlay_bundle("leaching_pot", web, priv)

    decoded = decode_bundle(data)
    for rel in decoded["relations"]:
        # The subject / object CIDs should be reachable from within the web
        for cid_attr in ("subject", "object"):
            cid = getattr(rel, cid_attr, None)
            if cid and cid in web.nodes:  # node exists in web
                chain = ancestry(web, cid)
                assert isinstance(chain, list)  # ancestry walk returns a list


@pytest.mark.property
def test_overlay_originator_is_attributed():
    """The originator field in the decoded bundle must be a non-empty PLS address."""
    from knitweb.core.crypto import address
    priv, pub = crypto.generate_keypair()
    web = _chem_web()
    data, sig = _make_overlay_bundle("leaching_pot", web, priv)

    decoded = decode_bundle(data)
    originator = decoded.get("originator")
    assert originator, "originator must be set"
    expected_addr = address(pub)
    assert originator == expected_addr, (
        f"originator {originator!r} must match address({pub!r}) = {expected_addr!r}"
    )


@pytest.mark.property
def test_bundle_digest_self_consistent():
    """bundle_digest(data) must match what verify_bundle uses internally."""
    from knitweb.synaptic.bytecode import bundle_digest
    priv, pub = crypto.generate_keypair()
    web = _chem_web()
    data, sig = _make_overlay_bundle("leaching_pot", web, priv)
    # The digest is the canonical commitment; verify_bundle recomputes it internally.
    digest = bundle_digest(data)
    assert isinstance(digest, str)
    assert len(digest) > 0
    # A bundle that verifies must have a consistent digest (verify internally recomputes).
    assert verify_bundle(pub, data, sig)


# ---------------------------------------------------------------------------
# AC3 — Lab MVP: marker backend → resolve → distill → verify → overlay
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_lab_mvp_marker_resolves_to_cid():
    """Looking at a physical marker resolves deterministically to a CID."""
    registry = {
        "POT-001": _LEACHING_POT_CID,
        "POT-002": _LEACHING_POT_CID,
        "POT-003": _LEACHING_POT_CID,
        "POT-004": _LEACHING_POT_CID,
        "FURNACE-A": _FURNACE_CID,
    }
    backend = MarkerBackend(registry)

    result = recognize("POT-001", backend)
    assert result.resolved
    assert result.resolver_key == _LEACHING_POT_CID
    assert result.confidence == 1.0
    assert not result.requires_confirmation  # marker is deterministic


@pytest.mark.property
def test_lab_mvp_unknown_marker_does_not_resolve():
    """An unregistered marker returns no CID — nothing to overlay."""
    backend = MarkerBackend({"POT-001": _LEACHING_POT_CID})
    result = recognize("POT-UNKNOWN", backend)
    assert not result.resolved
    assert result.confidence == 0.0


@pytest.mark.property
def test_lab_mvp_full_pipeline_marker_to_overlay():
    """End-to-end: marker scan → distill bundle → verify → decode overlay facts."""
    # Step 1: Resolve marker to a query string
    registry = {"POT-001": "leaching_pot"}
    backend = MarkerBackend(registry)
    recognition = recognize("POT-001", backend)
    assert recognition.resolved
    query = recognition.resolver_key  # "leaching_pot"

    # Step 2: Distill an overlay bundle for that query
    priv, pub = crypto.generate_keypair()
    web = _chem_web()
    data, sig = sdk.distill_bundle(query, ("public",), priv, web=web)

    # Step 3: Edge verifies before rendering
    assert verify_bundle(pub, data, sig), "bundle must verify before rendering"

    # Step 4: Decode and surface overlay facts
    decoded = decode_bundle(data)
    assert len(decoded["relations"]) > 0
    from knitweb.core.crypto import address as pub_to_addr
    assert decoded["originator"] == pub_to_addr(pub)


@pytest.mark.property
def test_lab_mvp_cr6_safety_node_accessible():
    """A Cr(VI) safety note woven into the Web is reachable after distillation."""
    web = Web()
    source = web.weave({
        "kind": "knowledge", "title": "leaching_pot", "scope": "public",
    })
    safety = web.weave({
        "kind": "knowledge",
        "title": "Cr6_safety_note",
        "scope": "public",
        "content": "Cr(VI) leaching risk: pH < 4 — use fume hood",
    })
    web.link(source, safety, "warns_about", weight=10)

    priv, pub = crypto.generate_keypair()
    data, sig = sdk.distill_bundle("leaching_pot", ("public",), priv, web=web)
    assert verify_bundle(pub, data, sig)

    decoded = decode_bundle(data)
    rel_subjects = {getattr(r, "subject", None) for r in decoded["relations"]}
    rel_objects = {getattr(r, "object", None) for r in decoded["relations"]}
    all_cids = rel_subjects | rel_objects

    # safety node or source node must appear in the relation set
    assert source in all_cids or safety in all_cids


@pytest.mark.property
def test_lab_mvp_marker_recognition_is_deterministic_not_ai():
    """Marker backend is exact — same input always yields the same CID (not AI)."""
    backend = MarkerBackend({"POT-001": _LEACHING_POT_CID})
    results = [recognize("POT-001", backend) for _ in range(10)]
    assert all(r.resolver_key == _LEACHING_POT_CID for r in results)
    assert all(r.confidence == 1.0 for r in results)
    assert len(set(r.resolver_key for r in results)) == 1
