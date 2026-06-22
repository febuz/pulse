"""Unit proofs for the Pulse operator CLI (:mod:`knitweb.tools.cli`).

Covers the pure read commands rounded out for issue #11: ``version`` (package
version) and ``world status`` (ledger size + state root + balance for a persisted
wallet), plus the top-level ``--help`` / dispatch wiring. The command bodies are
plain functions, so these run without spawning a subprocess.
"""

import json
import tomllib
from pathlib import Path

import pytest

import knitweb
from knitweb import store
from knitweb.ledger.node import AccountNode
from knitweb.tools import cli


def _pyproject_version():
    # tests/property/test_tools_cli.py -> repo root is two parents up.
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():  # pragma: no cover - only when running from a wheel
        pytest.skip("pyproject.toml not present (installed dist)")
    with pyproject.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


@pytest.mark.property
def test_dunder_version_is_single_sourced_from_pyproject():
    # Guard against version drift (#19): the in-tree __version__ (the static
    # fallback used when running from src) must byte-equal pyproject's version.
    assert knitweb.__version__ == _pyproject_version()


@pytest.mark.property
def test_static_fallback_equals_pyproject():
    # Even if a stray installed dist shadows __version__, the literal fallback
    # that ships in __init__.py must itself match pyproject.
    assert knitweb._VERSION_FALLBACK == _pyproject_version()


@pytest.mark.property
def test_package_version_falls_back_to_dunder_version():
    # Run straight from src (no installed dist) -> the in-tree __version__ is used.
    assert cli.package_version() == knitweb.__version__


@pytest.mark.property
def test_package_version_prefers_installed_distribution(monkeypatch):
    monkeypatch.setattr("importlib.metadata.version", lambda name: "9.9.9-test")
    assert cli.package_version() == "9.9.9-test"


@pytest.mark.property
def test_cmd_version_shape():
    record = cli.cmd_version()
    assert record == {"name": "knitweb", "version": cli.package_version()}


@pytest.mark.property
def test_cmd_world_status_reports_size_state_root_and_balance(tmp_path):
    wallet = str(tmp_path / "id.cbor")
    node = AccountNode(genesis_balances={"PLS": 17})
    store.save_node(node, wallet)

    status = cli.cmd_world_status(wallet)
    assert status["wallet"] == wallet
    assert status["address"] == node.address
    assert status["size"] == 1               # genesis fiber only
    assert status["state_root"] == node.braid.head.cid
    assert status["seq"] == 0
    assert status["nonce"] == 0
    assert status["balance_pls"] == 17


@pytest.mark.property
def test_cmd_world_status_state_root_is_account_state_commitment(tmp_path):
    # Two wallets with different balances commit to different state roots; the same
    # wallet reloaded commits to the same one (content-addressed, deterministic).
    a = str(tmp_path / "a.cbor")
    b = str(tmp_path / "b.cbor")
    store.save_node(AccountNode(genesis_balances={"PLS": 1}), a)
    store.save_node(AccountNode(genesis_balances={"PLS": 2}), b)

    root_a = cli.cmd_world_status(a)["state_root"]
    root_b = cli.cmd_world_status(b)["state_root"]
    assert root_a != root_b
    assert cli.cmd_world_status(a)["state_root"] == root_a   # stable across reloads


@pytest.mark.property
def test_main_version_prints_json(capsys):
    assert cli.main(["version"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "knitweb"
    assert out["version"] == cli.package_version()


@pytest.mark.property
def test_main_world_status_prints_json(tmp_path, capsys):
    wallet = str(tmp_path / "id.cbor")
    store.save_node(AccountNode(genesis_balances={"PLS": 5}), wallet)

    assert cli.main(["world", "status", "--wallet", wallet]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["balance_pls"] == 5
    assert out["size"] == 1
    assert out["state_root"].startswith("b")


@pytest.mark.property
def test_main_help_lists_commands_without_error(capsys):
    # Top-level --help exits 0 and advertises the new read commands + passthrough.
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    for token in ("identity", "host", "world", "version", "passthrough"):
        assert token in text


@pytest.mark.property
def test_main_operator_command_delegates_to_app_cli(tmp_path, monkeypatch):
    # Operator verbs are handed verbatim to knitweb.app.cli without touching argparse.
    seen = {}

    def fake_app_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("knitweb.app.cli.main", fake_app_main)
    assert cli.main(["balance", "--wallet", "w.cbor"]) == 0
    assert seen["argv"] == ["balance", "--wallet", "w.cbor"]
