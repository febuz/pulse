"""Proofs for the synaptic-compiler CLI: compile an OriginTrail asset → signed bytecode."""

import json

import pytest

from knitweb.app import cli
from knitweb.core import crypto


def _asset_file(tmp_path):
    asset = {
        "origintrail_id": 7,
        "originator": "Acme",
        "linked_sources": [{"type": "IFRS_File", "url": "https://ifrs.org"}],
    }
    p = str(tmp_path / "asset.json")
    with open(p, "w") as fh:
        json.dump(asset, fh)
    return p


@pytest.mark.property
def test_compile_writes_signed_bundle_that_verifies(tmp_path):
    priv, pub = crypto.generate_keypair()
    asset = _asset_file(tmp_path)
    out = str(tmp_path / "bundle.plsbc")
    asset_cid, sig = cli.cmd_compile(asset, priv, out)
    assert asset_cid and sig
    # the written bundle + sig verify against the originator key
    assert cli.cmd_verify_bundle(out, sig, pub)
    import os
    assert os.path.isfile(out) and os.path.isfile(out + ".sig")
    assert open(out + ".sig").read().strip() == sig


@pytest.mark.property
def test_verify_bundle_rejects_tamper_and_wrong_key(tmp_path):
    priv, pub = crypto.generate_keypair()
    out = str(tmp_path / "bundle.plsbc")
    _, sig = cli.cmd_compile(_asset_file(tmp_path), priv, out)
    # tamper the bundle bytes -> verification fails
    data = open(out, "rb").read()
    open(out, "wb").write(data[:-1] + bytes([data[-1] ^ 1]))
    assert not cli.cmd_verify_bundle(out, sig, pub)
    # wrong originator key -> fails even on the intact bundle
    open(out, "wb").write(data)
    _, other_pub = crypto.generate_keypair()
    assert not cli.cmd_verify_bundle(out, sig, other_pub)


@pytest.mark.property
def test_compile_accepts_a_wallet_file_as_the_key(tmp_path):
    # --key may be a persisted wallet file; _read_key extracts its private key
    wpath = str(tmp_path / "w.cbor")
    node = cli.cmd_wallet_new(wpath)
    out = str(tmp_path / "bundle.plsbc")
    _, sig = cli.cmd_compile(_asset_file(tmp_path), cli._read_key(wpath), out)
    assert cli.cmd_verify_bundle(out, sig, node.pub)


@pytest.mark.property
def test_main_compile_then_verify_exit_codes(tmp_path):
    priv, pub = crypto.generate_keypair()
    asset = _asset_file(tmp_path)
    out = str(tmp_path / "b.plsbc")
    assert cli.main(["compile", "--asset", asset, "--key", priv, "--out", out]) == 0
    sig = open(out + ".sig").read().strip()
    assert cli.main(["verify-bundle", "--bundle", out, "--sig", sig, "--originator", pub]) == 0
    _, other = crypto.generate_keypair()
    assert cli.main(["verify-bundle", "--bundle", out, "--sig", sig, "--originator", other]) == 1
