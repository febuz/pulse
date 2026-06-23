"""Property tests for the knitweb lens CLI surface."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from knitweb.app.cli import cmd_lens_digest
from knitweb.core import crypto
from knitweb.core.pulse import Pulse
from knitweb.fabric.web import Web
from knitweb.synaptic import bytecode as bc


def _bundle_path(tmp_path):
    orig_priv, _ = crypto.generate_keypair()
    asset = {
        "origintrail_id": 42,
        "originator": "TestCo",
        "linked_sources": [{"type": "IFRS_File", "url": "https://ifrs.org"}],
    }
    rels = [bc.Relation(str(asset["origintrail_id"]), "hasSource", "https://ifrs.org", "IFRS_File")]
    bundle = bc.compile_bundle(str(asset["origintrail_id"]), asset["originator"], rels)
    path = tmp_path / "bundle.pls"
    path.write_bytes(bundle)
    return str(path)


def _web_path(tmp_path):
    web = Web()
    a = web.weave({"n": "a"})
    b = web.weave({"n": "b"})
    web.link(a, b, "supports", weight=2)
    path = tmp_path / "web.json"
    path.write_text(
        json.dumps(
            {
                "nodes": [{"n": "a"}, {"n": "b"}],
                "edges": [{"src": a, "dst": b, "rel": "supports", "weight": 2}],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def _pulse_path(tmp_path):
    pulse = Pulse(interval_s=10, genesis_ts=1000)
    pulse.beat(1000, state_root="00")
    path = tmp_path / "pulse.json"
    path.write_text(
        json.dumps(
            {
                "interval_s": pulse.interval_s,
                "genesis_ts": pulse.genesis_ts,
                "beats": [
                    {
                        "epoch": b.epoch,
                        "timestamp": b.timestamp,
                        "state_root": b.state_root,
                        "prev_beat": b.prev_beat,
                    }
                    for b in pulse.beats
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


@pytest.mark.property
def test_lens_digest_bundle(tmp_path):
    path = _bundle_path(tmp_path)
    digest = cmd_lens_digest(bundle_path=path)
    assert "Knitweb Lens digest" in digest
    assert "Asset" in digest
    assert "TestCo" in digest


@pytest.mark.property
def test_lens_digest_web(tmp_path):
    path = _web_path(tmp_path)
    digest = cmd_lens_digest(web_path=path)
    assert "Knitweb Lens digest" in digest
    assert "Edge" in digest
    assert "supports" in digest


@pytest.mark.property
def test_lens_digest_pulse(tmp_path):
    path = _pulse_path(tmp_path)
    digest = cmd_lens_digest(pulse_path=path)
    assert "Knitweb Lens digest" in digest
    assert "Beat" in digest
    assert "epoch" in digest


@pytest.mark.property
def test_lens_digest_requires_exactly_one_source(tmp_path):
    with pytest.raises(SystemExit):
        cmd_lens_digest()
    with pytest.raises(SystemExit):
        cmd_lens_digest(bundle_path=_bundle_path(tmp_path), web_path=_web_path(tmp_path))
