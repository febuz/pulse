"""Proofs for `knitweb edge-load` — verify-before-trust loading on the edge/AR side."""

import json

import pytest

from knitweb.app import cli
from knitweb.core import crypto
from knitweb.edge.runtime import EdgeVerifyError


def _compiled(tmp_path):
    priv, pub = crypto.generate_keypair()
    asset = {"origintrail_id": 7, "originator": "Acme",
             "linked_sources": [{"type": "IFRS_File", "url": "https://ifrs.org"}]}
    ap = str(tmp_path / "asset.json")
    json.dump(asset, open(ap, "w"))
    out = str(tmp_path / "bundle.plsbc")
    _, sig = cli.cmd_compile(ap, priv, out)
    return out, sig, pub


@pytest.mark.property
def test_edge_load_verified_returns_relations(tmp_path):
    bundle, sig, pub = _compiled(tmp_path)
    info = cli.cmd_edge_load(bundle, pub, sig)
    assert info["verified"] is True
    assert info["relations"] >= 1
    assert isinstance(info["features"], dict) and info["features"]
    assert info["asset_cid"]


@pytest.mark.property
def test_edge_load_refuses_bad_signature(tmp_path):
    bundle, sig, _ = _compiled(tmp_path)
    _, wrong_pub = crypto.generate_keypair()
    with pytest.raises(EdgeVerifyError):
        cli.cmd_edge_load(bundle, wrong_pub, sig)   # verify-before-trust refuses


@pytest.mark.property
def test_edge_load_unverified_when_no_key(tmp_path):
    bundle, _, _ = _compiled(tmp_path)
    info = cli.cmd_edge_load(bundle)                 # no key/sig -> unverified load
    assert info["verified"] is False
    assert info["relations"] >= 1


@pytest.mark.property
def test_main_edge_load_exit_code(tmp_path):
    bundle, sig, pub = _compiled(tmp_path)
    assert cli.main(["edge-load", "--bundle", bundle, "--originator", pub, "--sig", sig]) == 0
