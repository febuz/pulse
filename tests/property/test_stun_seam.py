"""STUN hole-punch seam: configure message sent on first listen, empty list."""

from __future__ import annotations

import pytest

from knitweb.p2p.webrtc_transport import WorkerBridge, pyodide_bridge


class _CaptureBridge(WorkerBridge):
    """WorkerBridge that captures posted messages."""

    def __init__(self):
        self.posted: list[dict] = []
        self._inbound = None
        self._fault = None

    def _post(self, msg):
        self.posted.append(msg)

    async def dial_frame(self, peer_key, rid, frame):
        raise NotImplementedError

    def respond_frame(self, peer_key, rid, frame):
        pass

    def set_inbound(self, callback):
        self._inbound = callback

    def set_frame_fault(self, callback):
        self._fault = callback

    async def close(self):
        pass

    def local_params(self):
        return {"pubkey": "test", "mailbox": "mb"}


def _make_spy_bridge(stun_servers=("stun:stun.l.google.com:19302",)):
    """Build a _PyodideBridge via pyodide_bridge() with a captured post list."""
    posted = []

    def post_to_shell(msg):
        posted.append(msg)

    bridge = pyodide_bridge(
        post_to_shell,
        self_key="pub_test",
        mailbox="mb_test",
        stun_servers=stun_servers,
    )
    return bridge, posted


@pytest.mark.property
def test_stun_configure_sent_on_set_inbound():
    """pyodide_bridge posts webrtc_configure on first set_inbound call."""
    bridge, posted = _make_spy_bridge()

    async def _dummy_handler(peer_key, rid, frame):
        pass

    bridge.set_inbound(_dummy_handler)

    assert len(posted) == 1
    msg = posted[0]
    assert msg["op"] == "webrtc_configure"
    assert isinstance(msg["stunServers"], list)
    assert "stun:stun.l.google.com:19302" in msg["stunServers"]


@pytest.mark.property
def test_stun_configure_sent_only_once():
    """set_inbound called twice only posts configure once."""
    bridge, posted = _make_spy_bridge()

    async def _dummy(pk, rid, frame):
        pass

    bridge.set_inbound(_dummy)
    bridge.set_inbound(_dummy)  # second call — no extra configure

    configure_msgs = [m for m in posted if m.get("op") == "webrtc_configure"]
    assert len(configure_msgs) == 1


@pytest.mark.property
def test_stun_empty_list_sends_empty_array():
    """Empty stun_servers sends stunServers=[] (not null/missing)."""
    bridge, posted = _make_spy_bridge(stun_servers=())

    async def _dummy(pk, rid, frame):
        pass

    bridge.set_inbound(_dummy)

    configure_msgs = [m for m in posted if m.get("op") == "webrtc_configure"]
    assert len(configure_msgs) == 1
    assert configure_msgs[0]["stunServers"] == []


@pytest.mark.property
def test_worker_bridge_configure_noop_by_default():
    """WorkerBridge base class configure() is a no-op (no exception)."""
    bridge = _CaptureBridge()
    bridge.configure({"op": "webrtc_configure", "stunServers": []})  # must not raise
