"""BootstrapRegistry: region-aware relay discovery, integer timestamps, prune."""

from __future__ import annotations

import json

import pytest

from knitweb.p2p.bootstrap import BootstrapRegistry


@pytest.mark.property
def test_bootstrap_announce_and_nearest_basic():
    reg = BootstrapRegistry()
    reg.announce("https://eu-west.example.com", "pub1", "eu-west")
    reg.announce("https://us-east.example.com", "pub2", "us-east")

    result = reg.nearest_relays("eu-west", n=2)
    assert "https://eu-west.example.com" in result
    assert len(result) <= 2


@pytest.mark.property
def test_bootstrap_last_seen_is_integer():
    reg = BootstrapRegistry()
    reg.announce("https://r1.example.com", "pub1", "eu")
    entries = reg.all_entries()
    assert len(entries) == 1
    assert isinstance(entries[0]["last_seen_ms"], int)
    assert "." not in str(entries[0]["last_seen_ms"])


@pytest.mark.property
def test_bootstrap_nearest_returns_at_most_n():
    reg = BootstrapRegistry()
    for i in range(5):
        reg.announce(f"https://r{i}.example.com", f"pub{i}", "eu")
    result = reg.nearest_relays("eu", n=3)
    assert len(result) <= 3


@pytest.mark.property
def test_bootstrap_prune_removes_stale(tmp_path):
    reg = BootstrapRegistry()
    # Inject a stale entry manually
    reg._entries.append({
        "region": "ap",
        "relay_url": "https://old.example.com",
        "pubkey": "oldpub",
        "last_seen_ms": 1000,  # epoch + 1s — definitely stale
    })
    reg.announce("https://fresh.example.com", "freshpub", "eu")
    removed = reg.prune(older_than_ms=1_000_000_000_000)  # prune anything older than 1B ms from now
    assert removed == 1
    urls = [e["relay_url"] for e in reg.all_entries()]
    assert "https://old.example.com" not in urls
    assert "https://fresh.example.com" in urls


@pytest.mark.property
def test_bootstrap_persists_to_json(tmp_path):
    path = str(tmp_path / "bootstrap.json")
    reg = BootstrapRegistry(path=path)
    reg.announce("https://r1.example.com", "pub1", "eu")

    # Load fresh instance from the same file
    reg2 = BootstrapRegistry(path=path)
    entries = reg2.all_entries()
    assert len(entries) == 1
    assert entries[0]["relay_url"] == "https://r1.example.com"
    assert isinstance(entries[0]["last_seen_ms"], int)
