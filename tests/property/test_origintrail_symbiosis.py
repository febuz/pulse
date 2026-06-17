"""Proof of the OriginTrail symbiosis round-trip — Knitweb's USP, end to end.

Knowledge flows *in* from OriginTrail, is compiled to verifiable signed edge bytecode,
woven into the fabric and checkpointed, and the resulting state is anchored back *out*
to OriginTrail — a closed, offline-verifiable provenance loop:

    resolve_asset (read DKG) → compile_bundle + sign (verifiable edge bytecode)
      → weave into Web → checkpoint on a Pulse beat
      → anchor back to OriginTrail (content-derived UAL) → resolve + verify

Every hop is content-addressed and signed, so a device that trusts neither the spider
nor the notary can still verify the whole chain from the artifacts alone.
"""

import pytest

from knitweb.anchor import Notary, verify_receipt
from knitweb.anchor.origintrail import OriginTrailAnchorBackend, ual
from knitweb.core import crypto
from knitweb.core.pulse import Pulse
from knitweb.fabric.items import checkpoint, web_state_root
from knitweb.fabric.web import Web
from knitweb.synaptic import bytecode as bc
from knitweb.synaptic.origintrail import resolve_asset


def _asset():
    return {
        "origintrail_id": 7,
        "originator": "Acme",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "Assay_Report", "url": "https://lab.example/ore-assay"},
        ],
    }


@pytest.mark.property
def test_origintrail_read_compile_anchor_resolve_round_trip():
    # 1. READ — resolve a DKG Knowledge Asset into provenance relations.
    asset_id, originator, relations = resolve_asset(_asset())
    assert relations  # the linked sources became relations

    # 2. COMPILE — verified relations -> deterministic, signed edge bytecode (the USP).
    orig_priv, orig_pub = crypto.generate_keypair()
    data = bc.compile_bundle(asset_id, originator, relations)
    sig = bc.sign_bundle(orig_priv, data)
    assert bc.verify_bundle(orig_pub, data, sig)            # originator-verifiable offline
    assert bc.compile_bundle(asset_id, originator, relations) == data  # deterministic

    # 3. WEAVE — record the compiled-knowledge artifact into the fabric.
    web = Web()
    record = {
        "kind": "compiled-knowledge",
        "asset_id": asset_id,
        "originator": originator,
        "bundle_digest": bc.bundle_digest(data),
    }
    cid = web.weave(record)
    assert cid in web.nodes

    # 4. CHECKPOINT — anchor the fabric state to a Pulse beat.
    pulse = Pulse(interval_s=60, genesis_ts=0)
    beat = pulse.beat(timestamp=120, state_root=web_state_root(web))
    cp = checkpoint(web, beat)

    # 5. ANCHOR BACK — publish the checkpoint state to OriginTrail; verify the receipt.
    backend = OriginTrailAnchorBackend()
    receipt = Notary(crypto.generate_keypair()[0]).anchor(cp, backend, timestamp=200)
    assert verify_receipt(receipt, cp)
    assert receipt.external_ref == ual(cp.state_root, 200)   # verifier-recomputable

    # 6. RESOLVE — the published DKG assertion carries the checkpoint root.
    resolved = backend.resolve(receipt.external_ref)
    assert resolved is not None and resolved["stateRoot"] == cp.state_root


@pytest.mark.property
def test_tampered_bundle_breaks_the_chain():
    asset_id, originator, relations = resolve_asset(_asset())
    orig_priv, orig_pub = crypto.generate_keypair()
    data = bc.compile_bundle(asset_id, originator, relations)
    sig = bc.sign_bundle(orig_priv, data)
    tampered = data[:-1] + bytes([data[-1] ^ 1])
    assert not bc.verify_bundle(orig_pub, tampered, sig)     # the verified-knowledge hop fails
