"""gateway.App backed by FabricNode shares one Web across processes."""

from __future__ import annotations

import multiprocessing as mp
import queue

import pytest

from knitweb.gateway import App


def _producer(out, stop) -> None:
    with App("producer", listen=("127.0.0.1", 0)) as app:
        address = app.fabric_address
        assert address is not None
        out.put({"port": address.port})
        app.attest("alice", {"kind": "reaction", "formula": "H2O"})
        app.link("H2O", "water", "is", weight=2)
        out.put({"state": app.web_state()})
        stop.wait(10)


def _consumer(port: int, out) -> None:
    with App("consumer", listen=("127.0.0.1", 0)) as app:
        added = app.sync_from(("127.0.0.1", port))
        out.put({"added": added, "state": app.web_state()})


def _get(out, timeout: int = 15):
    try:
        return out.get(timeout=timeout)
    except queue.Empty as exc:
        raise AssertionError("timed out waiting for gateway p2p worker") from exc


@pytest.mark.interop
def test_two_app_instances_share_web_via_fabric_broadcast():
    with App("a", listen=("127.0.0.1", 0)) as a, App("b", listen=("127.0.0.1", 0)) as b:
        assert b.fabric_address is not None
        a.add_peer("b", b.fabric_address)

        a.attest("alice", {"kind": "reaction", "formula": "CO2"})
        a.link("CO2", "carbon dioxide", "is", weight=2)

        expected = a.web_state()
        actual = b.web_state()
        assert actual["nodes"] == expected["nodes"]
        assert actual["edges"] == expected["edges"]
        assert actual["state_root"] == expected["state_root"]


@pytest.mark.interop
def test_two_app_processes_share_one_p2p_backed_web():
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    stop = ctx.Event()
    producer = ctx.Process(target=_producer, args=(out, stop))
    producer.start()
    consumer = None
    try:
        first = _get(out)
        assert "port" in first
        expected = _get(out)["state"]

        consumer = ctx.Process(target=_consumer, args=(first["port"], out))
        consumer.start()
        actual = _get(out)
    finally:
        stop.set()
        producer.join(timeout=10)
        if producer.is_alive():
            producer.terminate()
            producer.join(timeout=5)
        if consumer is not None:
            consumer.join(timeout=10)
            if consumer.is_alive():
                consumer.terminate()
                consumer.join(timeout=5)

    assert producer.exitcode == 0
    assert consumer is not None and consumer.exitcode == 0
    assert actual["added"] >= expected["nodes"]
    assert actual["state"]["nodes"] == expected["nodes"]
    assert actual["state"]["edges"] == expected["edges"]
    assert actual["state"]["state_root"] == expected["state_root"]
