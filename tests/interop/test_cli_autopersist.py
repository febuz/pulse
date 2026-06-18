"""Proofs that the node daemon auto-persists (closes the crash-gap in run_node).

Before this, `run_node` only persisted on clean shutdown, so a crash lost every Knit
received since startup. The autosave loop snapshots whenever the braid head changes,
so a received payment is on disk before any shutdown.
"""

import asyncio

import pytest

from knitweb import store
from knitweb.app import cli
from knitweb.ledger.node import AccountNode
from knitweb.p2p.node import AsyncioP2PNode


@pytest.mark.property
def test_autosave_once_saves_on_change_and_noops_otherwise(tmp_path):
    a = AccountNode(genesis_balances={"PLS": 100})
    b = AccountNode()
    p = str(tmp_path / "a.cbor")
    store.save_node(a, p)
    last = a.braid.head.cid

    # unchanged -> no-op, returns same head
    assert cli._autosave_once(a, p, last) == last

    # state changes -> persists the new state, returns the new head
    a.transfer_to(b, "PLS", 10, timestamp=1)
    new = cli._autosave_once(a, p, last)
    assert new == a.braid.head.cid != last
    assert store.load_node(p).balance("PLS") == 90


@pytest.mark.interop
def test_daemon_autopersists_received_knit_without_clean_shutdown(tmp_path):
    payer_path = str(tmp_path / "payer.cbor")
    payee_path = str(tmp_path / "payee.cbor")
    cli.cmd_wallet_new(payer_path, genesis=100)
    payee = cli.cmd_wallet_new(payee_path, genesis=0)

    async def scenario():
        payee_node = store.load_node(payee_path)
        server = AsyncioP2PNode(account=payee_node, host="127.0.0.1", port=0)
        await server.start()
        stop = asyncio.Event()
        saver = asyncio.create_task(
            cli._autosave_loop(payee_node, payee_path, stop, poll_s=0.01)
        )
        try:
            await cli.cmd_pay(payer_path, (server.host, server.port), payee.pub, 30, timestamp=1)
            await asyncio.sleep(0.05)  # let the autosave loop run a poll
            # Read the file as if the daemon crashed NOW (no clean shutdown):
            persisted = store.load_node(payee_path)
            assert persisted.balance("PLS") == 30   # received knit already on disk
        finally:
            stop.set()
            await saver
            await server.stop()

    asyncio.run(scenario())
    assert cli.cmd_balance(payee_path) == 30
