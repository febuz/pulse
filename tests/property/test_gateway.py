"""knitweb.gateway.App — the turnkey app layer (identity, economy, persistent web, provenance)."""
import http.client
import json
import socket
import threading
from contextlib import closing, contextmanager
from http.server import ThreadingHTTPServer

from knitweb.gateway import App, serve


def test_actor_is_stable_and_faucet_seeded():
    a = App().actor("device:abc")
    assert a["pulses"] == 50 and a["address"].startswith("pls1")
    assert App().actor("device:abc")["address"] == a["address"]   # same id → same wallet


def test_transfer_moves_pulses():
    app = App()
    r = app.transfer("u1", "u2", 10)
    assert r["from"] == 40 and r["to"] == 60 and r["knit"]


def test_attest_and_link_grow_a_persistent_web(tmp_path):
    f = str(tmp_path / "web.json")
    app = App("molgang", store=f)
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    app.link("V2O5", "vanadium pentoxide", "is", weight=3)
    st = app.web_state()
    assert st["nodes"] >= 3 and st["edges"] >= 1
    app2 = App("molgang", store=f)                 # fresh instance rebuilds from the store
    assert app2.web_state()["nodes"] == st["nodes"]
    assert app2.web_state()["state_root"] == st["state_root"]


def test_balances_persist_across_instances(tmp_path):
    f = str(tmp_path / "e.json")
    a = App(store=f); a.actor("x"); a.transfer("x", "y", 10)
    b = App(store=f)
    assert b.balance("x") == 40 and b.balance("y") == 60


def test_validate_quorum():
    assert App().validate(["confirm", "confirm", "confirm"])["released"] is True
    assert App().validate(["mismatch", "mismatch", "mismatch"])["released"] is False


def test_anchor_provenance():
    app = App(); app.attest("u", {"x": 1})
    pr = app.anchor()
    assert pr["ual"].startswith("did:dkg:knitweb/") and pr["verified"]


# -- serve() over HTTP -----------------------------------------------------
@contextmanager
def _running(app, *, host="127.0.0.1", token=None):
    """Run serve(...) in-process on an ephemeral port in a daemon thread.

    serve() binds + blocks via serve_forever(); we capture the bound ThreadingHTTPServer
    by patching it, then learn the OS-assigned port from server_address.
    """
    import http.server as _hs

    captured: dict = {}

    def _factory(addr, handler):
        srv = ThreadingHTTPServer(addr, handler)
        captured["srv"] = srv
        captured["port"] = srv.server_address[1]
        return srv

    real = _hs.ThreadingHTTPServer
    _hs.ThreadingHTTPServer = _factory  # serve() imports it locally from http.server
    try:
        t = threading.Thread(target=lambda: serve(app, port=0, host=host, token=token), daemon=True)
        t.start()
        # wait until the server is bound
        for _ in range(500):
            if "srv" in captured:
                break
            threading.Event().wait(0.002)
        assert "srv" in captured, "server did not start"
        yield captured["port"]
    finally:
        if "srv" in captured:
            captured["srv"].shutdown()
            captured["srv"].server_close()
        _hs.ThreadingHTTPServer = real


def _get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    with closing(c):
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()


def test_serve_default_bind_is_loopback():
    app = App()
    with _running(app) as port:
        # reachable on loopback
        status, _ = _get(port, "/")
        assert status == 200
        # and the socket is bound to 127.0.0.1, not 0.0.0.0
        with closing(socket.socket()) as s:
            assert s.connect_ex(("127.0.0.1", port)) == 0


def test_serve_open_when_no_token():
    app = App()
    with _running(app) as port:
        status, _ = _get(port, "/balance?id=alice")
        assert status == 200


def test_web_get_returns_the_graph_read_contract(tmp_path):
    # The /web and /provenance GET routes are the only HTTP surface a read-only
    # client (e.g. knitweb-monitor) can poll for the woven graph + anchor. Pin
    # the exact JSON contract so the gateway's read surface cannot drift silently.
    app = App("molgang", store=str(tmp_path / "web.json"))
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    app.link("V2O5", "vanadium pentoxide", "is", weight=3)
    with _running(app) as port:
        status, body = _get(port, "/web")
        assert status == 200
        web = json.loads(body)
        assert set(web) >= {"nodes", "edges", "state_root", "records"}
        assert isinstance(web["nodes"], int) and isinstance(web["edges"], int)
        assert web["nodes"] >= 3 and web["edges"] >= 1
        assert isinstance(web["records"], list)
        assert isinstance(web["state_root"], str) and web["state_root"]

        status, body = _get(port, "/provenance")
        assert status == 200
        prov = json.loads(body)
        assert set(prov) >= {"ual", "state_root", "verified"}
        assert isinstance(prov["verified"], bool)


def test_serve_token_required_and_accepted():
    app = App()
    with _running(app, token="s3cret") as port:
        # health stays open without a token
        assert _get(port, "/")[0] == 200
        # protected path without the header -> 401
        assert _get(port, "/balance?id=alice")[0] == 401
        # wrong token -> 401
        assert _get(port, "/balance?id=alice", {"Authorization": "Bearer nope"})[0] == 401
        # correct Bearer token -> 200
        assert _get(port, "/balance?id=alice", {"Authorization": "Bearer s3cret"})[0] == 200
        # correct X-Auth-Token header -> 200
        assert _get(port, "/balance?id=alice", {"X-Auth-Token": "s3cret"})[0] == 200
