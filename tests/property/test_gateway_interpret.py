"""The `/interpret` Lens delegation hook — a strictly read-only gateway extension point.

These proofs pin the three acceptance properties of knitweb/pulse#157:
  (a) Pulse/gateway constructs and serves with NO Lens registered (no import error, all
      existing endpoints fine, ``/interpret`` returns the documented not-installed contract);
  (b) a registered (in-test) Lens callable is delegated to read-only and its result returned;
  (c) the ``/interpret`` path mutates neither the Web nor any App state.
"""
import http.client
import json
import threading
from contextlib import closing, contextmanager
from http.server import ThreadingHTTPServer

import pytest

from knitweb.fabric.items import web_state_root
from knitweb.fabric.snapshot import web_snapshot
from knitweb.gateway import App, serve


# -- in-process HTTP harness (mirrors tests/property/test_gateway.py) -------
@contextmanager
def _running(app, *, host="127.0.0.1", token=None):
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


def _post(port, path, body, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    with closing(c):
        payload = json.dumps(body).encode()
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request("POST", path, body=payload, headers=h)
        r = c.getresponse()
        return r.status, r.read()


# -- (a) Pulse runs and serves with NO Lens installed -----------------------
@pytest.mark.property
def test_app_constructs_and_runs_without_a_lens():
    app = App()
    assert app.has_lens is False
    # no import of any LLM/vector/graph-DB library is forced by the hook
    out = app.interpret("what is acid?")
    assert out == {"ok": False, "lens": False, "reason": "no-interpreter-installed",
                   "query": "what is acid?", "nodes": 0, "edges": 0}


@pytest.mark.property
def test_existing_endpoints_unaffected_without_a_lens():
    # The Lens seam must not break any pre-existing endpoint.
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    with _running(app) as port:
        assert _get(port, "/")[0] == 200
        assert _get(port, "/balance?id=alice")[0] == 200
        assert _get(port, "/web")[0] == 200
        assert _get(port, "/provenance")[0] == 200


@pytest.mark.property
def test_interpret_without_lens_returns_501_contract_over_http():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    with _running(app) as port:
        status, body = _post(port, "/interpret", {"query": "find water"})
        assert status == 501
        out = json.loads(body)
        assert out["ok"] is False and out["lens"] is False
        assert out["reason"] == "no-interpreter-installed"
        assert out["query"] == "find water"
        assert isinstance(out["nodes"], int) and isinstance(out["edges"], int)


# -- (b) a registered Lens is delegated to, read-only -----------------------
def _echo_lens(query, snapshot, params):
    """A trivial in-test Lens: reads the snapshot + params, writes nothing back."""
    matched = sorted(cid for cid, rec in snapshot["records"].items() if query in str(rec))
    return {"query_echo": query, "nodes_seen": snapshot["node_count"],
            "matched": matched, "params_echo": dict(params)}


@pytest.mark.property
def test_interpret_delegates_to_registered_lens_in_process():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O", "text": "water"})
    app.set_lens(_echo_lens)
    assert app.has_lens is True
    out = app.interpret("water")
    assert out["ok"] is True and out["lens"] is True
    assert out["query"] == "water"
    assert out["result"]["query_echo"] == "water"
    assert out["result"]["nodes_seen"] == app.web.size[0]
    assert len(out["result"]["matched"]) == 1   # fixture weaves exactly one 'water' record


@pytest.mark.property
def test_interpret_delegates_to_registered_lens_over_http():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O", "text": "water"})
    app.set_lens(_echo_lens)
    with _running(app) as port:
        status, body = _post(port, "/interpret", {"query": "water"})
        assert status == 200
        out = json.loads(body)
        assert out["ok"] is True and out["lens"] is True
        assert out["result"]["query_echo"] == "water"


@pytest.mark.property
def test_set_lens_none_clears_the_hook():
    app = App()
    app.set_lens(_echo_lens)
    assert app.has_lens is True
    app.set_lens(None)
    assert app.has_lens is False
    assert app.interpret("x")["reason"] == "no-interpreter-installed"


# -- (c) the /interpret path performs no mutation ---------------------------
@pytest.mark.property
def test_interpret_does_not_mutate_web_or_app_state():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    app.link("V2O5", "vanadium pentoxide", "is", weight=3)

    root_before = web_state_root(app.web)
    size_before = app.web.size
    records_before = list(app._records)
    record_values_before = web_snapshot(app.web)["records"]  # deep copy of record interiors

    # a Lens that *tries* to mutate the snapshot it is given must not reach the live Web —
    # including the interiors of individual records, not just the top-level containers.
    def _hostile_lens(query, snapshot, params):
        for rec in snapshot["records"].values():
            if isinstance(rec, dict):
                for k in list(rec):
                    rec[k] = "EVIL"            # tamper nested record values
        snapshot["records"].clear()
        snapshot["node_count"] = -1
        return {"tampered": True}

    app.set_lens(_hostile_lens)
    out = app.interpret("anything")
    assert out["ok"] is True

    assert web_state_root(app.web) == root_before
    assert app.web.size == size_before
    assert app._records == records_before
    # deep isolation: nested record values are untouched. web_state_root commits only to
    # CID keys/edges, so a value-level tamper would slip past it — compare contents directly.
    assert web_snapshot(app.web)["records"] == record_values_before


@pytest.mark.property
def test_interpret_over_http_leaves_web_state_root_unchanged():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    app.set_lens(_echo_lens)
    with _running(app) as port:
        before = json.loads(_get(port, "/web")[1])["state_root"]
        assert _post(port, "/interpret", {"query": "water"})[0] == 200
        after = json.loads(_get(port, "/web")[1])["state_root"]
        assert before == after


@pytest.mark.property
def test_interpret_respects_auth_token():
    app = App()
    app.set_lens(_echo_lens)
    with _running(app, token="s3cret") as port:
        # protected like every non-health route
        assert _post(port, "/interpret", {"query": "x"})[0] == 401
        ok = _post(port, "/interpret", {"query": "x"}, {"Authorization": "Bearer s3cret"})
        assert ok[0] == 200


# -- a Lens that raises is contained — the gateway must keep serving ---------
def _raising_lens(query, snapshot, params):
    """A Lens whose backend fails — the normal failure mode of an external interpreter."""
    raise RuntimeError("backend timeout talking to the model service")


@pytest.mark.property
def test_interpret_contains_lens_exception_with_deterministic_contract():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    app.set_lens(_raising_lens)
    # No exception escapes; the contract is deterministic and leaks no internal error text.
    out = app.interpret("boom")
    assert out == {"ok": False, "lens": True, "reason": "interpreter-error", "query": "boom"}


@pytest.mark.property
def test_interpret_lens_error_is_502_and_gateway_keeps_serving_over_http():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    app.set_lens(_raising_lens)
    with _running(app) as port:
        status, body = _post(port, "/interpret", {"query": "boom"})
        # an upstream interpreter fault is a deterministic 502 — NOT a 400 nor a dropped
        # connection with no response.
        assert status == 502
        assert json.loads(body) == {"ok": False, "lens": True,
                                    "reason": "interpreter-error", "query": "boom"}
        # the gateway is still serving after the Lens blew up
        assert _get(port, "/")[0] == 200
        assert _post(port, "/interpret", {"query": "again"})[0] == 502


# -- params are forwarded to the Lens (not silently dropped) -----------------
@pytest.mark.property
def test_interpret_forwards_params_to_lens():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O", "text": "water"})
    app.set_lens(_echo_lens)
    out = app.interpret("water", {"top_k": 3, "lang": "en"})
    assert out["ok"] is True
    assert out["result"]["params_echo"] == {"top_k": 3, "lang": "en"}
    # default: no params -> the Lens receives an empty dict, never None
    assert app.interpret("water")["result"]["params_echo"] == {}


@pytest.mark.property
def test_interpret_forwards_params_over_http():
    app = App()
    app.attest("u1", {"kind": "reaction", "formula": "H2O", "text": "water"})
    app.set_lens(_echo_lens)
    with _running(app) as port:
        status, body = _post(port, "/interpret", {"query": "water", "params": {"top_k": 2}})
        assert status == 200
        assert json.loads(body)["result"]["params_echo"] == {"top_k": 2}
