"""Proofs for the runnable node + CLI (M2): wallet persistence + pay over the wire.

Exercises the CLI command bodies directly (no subprocess): create persisted wallets,
run a recipient node daemon in-process, pay from one wallet to the other over the
stdlib-asyncio P2P node, and confirm both sides' balances are persisted and conserved.
"""

import asyncio
import json

import pytest

from knitweb import store
from knitweb.app import cli


@pytest.mark.property
def test_wallet_create_persists_address_and_balance(tmp_path):
    p = str(tmp_path / "w.cbor")
    node = cli.cmd_wallet_new(p, genesis=42)
    addr, pub = cli.cmd_address(p)
    assert addr == node.address and pub == node.pub
    assert cli.cmd_balance(p) == 42
    # reloading is stable (same address across processes)
    assert cli.cmd_address(p)[0] == addr


@pytest.mark.interop
def test_pulse_identity_and_host_status_json_contract(tmp_path, capsys):
    wallet = str(tmp_path / "identity.cbor")

    assert cli.main(["identity", "create", "--out", wallet, "--genesis", "11", "--json"]) == 0
    identity = json.loads(capsys.readouterr().out)
    assert identity["kind"] == "identity"
    assert identity["created"] is True
    assert identity["balance"] == 11
    assert identity["address"].startswith("pls1")
    assert identity["publicKey"]
    assert "secret" not in identity and "privateKey" not in identity and "priv" not in identity

    assert cli.main([
        "host", "status", "--identity", wallet, "--listen", "127.0.0.1:8765", "--json"
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status == {
        "address": identity["address"],
        "balance": 11,
        "identity": wallet,
        "kind": "host-status",
        "listen": "127.0.0.1:8765",
        "pages": 0,
    }


@pytest.mark.interop
def test_pulse_page_publish_json_contract(tmp_path, capsys):
    wallet = str(tmp_path / "identity.cbor")
    pages = str(tmp_path / "pages")
    cli.cmd_identity_create(wallet, genesis=1)

    assert cli.main([
        "page", "publish",
        "--identity", wallet,
        "--out", pages,
        "--title", "Hello Pulse",
        "--body", "First page",
        "--json",
    ]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["kind"] == "page"
    assert page["title"] == "Hello Pulse"
    assert page["author"].startswith("pls1")
    assert page["cid"].startswith("b")
    assert page["path"].startswith(pages)


@pytest.mark.interop
def test_pay_over_p2p_persists_both_sides(tmp_path):
    payer_path = str(tmp_path / "payer.cbor")
    payee_path = str(tmp_path / "payee.cbor")
    cli.cmd_wallet_new(payer_path, genesis=100)
    payee = cli.cmd_wallet_new(payee_path, genesis=0)
    payee_pub = payee.pub

    async def scenario():
        from knitweb.p2p.node import AsyncioP2PNode
        payee_node = store.load_node(payee_path)
        server = AsyncioP2PNode(account=payee_node, host="127.0.0.1", port=0)
        await server.start()  # port 0 -> OS assigns; read it back from the node
        try:
            knit_id = await cli.cmd_pay(
                payer_path, (server.host, server.port), payee_pub, amount=30, timestamp=1
            )
            assert knit_id
        finally:
            store.save_node(payee_node, payee_path)  # persist received knit
            await server.stop()

    asyncio.run(scenario())

    # Both sides persisted and value conserved.
    assert cli.cmd_balance(payer_path) == 70
    assert cli.cmd_balance(payee_path) == 30


@pytest.mark.interop
def test_pay_overdraft_is_refused_and_state_unchanged(tmp_path):
    payer_path = str(tmp_path / "payer.cbor")
    payee_path = str(tmp_path / "payee.cbor")
    cli.cmd_wallet_new(payer_path, genesis=5)
    payee = cli.cmd_wallet_new(payee_path, genesis=0)

    async def scenario():
        from knitweb.p2p.node import AsyncioP2PNode, P2PError
        payee_node = store.load_node(payee_path)
        server = AsyncioP2PNode(account=payee_node, host="127.0.0.1", port=0)
        await server.start()
        try:
            with pytest.raises(P2PError):
                await cli.cmd_pay(
                    payer_path, (server.host, server.port), payee.pub, amount=999, timestamp=1
                )
        finally:
            await server.stop()

    asyncio.run(scenario())
    assert cli.cmd_balance(payer_path) == 5      # unchanged
    assert cli.cmd_balance(payee_path) == 0
