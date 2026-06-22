"""knitweb.gateway — the turnkey **app layer** for building on the knitweb.

Building a real app on knitweb's primitives (MOLGANG was the proving ground) surfaces the
same gaps *every* builder hits — the low-level layers are deliberately minimal, but an app
shouldn't have to re-solve all of this:

  1. **Identity from an external id.** Apps have their own users (a Roblox UserId, a
     username). There was no way to get a *stable* knitweb account for one without managing
     keys → `App.actor(external_id)` (built on `AccountNode.from_seed`).
  2. **A persistent, shared web.** The fabric `Web` is in-memory with no save/load, so the
     woven knowledge vanishes on restart and can't be shared between processes → `App` persists
     every woven record/edge, and can back the Web with a p2p `FabricNode`
     (`listen=`, `peers=`, `sync_from`) so separate processes converge.
  3. **Turnkey economy / validation / provenance.** Faucet, balances, transfers, BFT-quorum
     validation, and OriginTrail anchoring all existed as separate primitives that each app
     re-wired → `App` exposes them as one intuitive object.
  4. **Any runtime.** `serve(app)` exposes the whole thing over plain HTTP/JSON so a FastAPI,
     a Colyseus server, or a Roblox HttpService can drive it — not just Python.

`App` is the answer: identity + economy + a persistent shared web + validation + provenance,
in one object, in ~a dozen intuitive methods.

SECURITY
--------
* ``serve(...)`` binds **127.0.0.1** by default — it is *not* exposed to the LAN/internet
  unless you explicitly pass ``host="0.0.0.0"`` (which prints a warning). Pass a ``token=``
  to require ``Authorization: Bearer <token>`` (or ``X-Auth-Token``) on every request;
  without a token the gateway runs open and logs a one-line dev-only notice.
* ``App.actor`` / ``AccountNode.from_seed`` accounts are **deterministic from the seed**:
  the seed *is* the private key. That is exactly what you want for app/bridge/dev
  identities, but it means a known seed = a spendable key. Keep seeds secret and never use
  ``from_seed`` identities for high-value custody.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable, Mapping

from .anchor import Notary
from .anchor.origintrail import OriginTrailAnchorBackend
from .core.pulse import Pulse
from .fabric.items import checkpoint, web_state_root
from .fabric.node import FabricNode
from .fabric.snapshot import web_snapshot
from .fabric.web import Web
from .ledger.node import AccountNode
from .p2p.node import PeerAddress
from .pouw import quorum

# A *Lens* is any host-supplied callable that interprets a query against a read-only
# Web snapshot. It is injected from outside Pulse (see ``App.set_lens``) so that an LLM /
# vector / graph-DB interpreter can live in a separate service or package — Pulse itself
# never imports one and adds no dependency for it. The hook is a pure delegation seam.
Lens = Callable[[str, Mapping, Mapping], object]

_NOTARY_PRIV = "0" * 63 + "1"  # fixed dev notary → reproducible UAL per web state


PeerSpec = PeerAddress | tuple[str, int]


def _peer(peer: PeerSpec) -> PeerAddress:
    if isinstance(peer, PeerAddress):
        return peer
    host, port = peer
    return PeerAddress(str(host), int(port))


class _FabricRuntime:
    """Run an async FabricNode behind the synchronous App API."""

    def __init__(self, node: FabricNode) -> None:
        self.node = node
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run_loop,
            name="knitweb-gateway-fabric",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("fabric runtime did not start")
        self.run(self.node.start())

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def run(self, coro):
        if self._closed:
            raise RuntimeError("fabric runtime is closed")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=10)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.run(self.node.stop())
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            self._loop.close()


class App:
    """A knitweb application: stable identities, a token economy, a persistent shared web,
    peer validation and provenance — all wired for you."""

    def __init__(
        self,
        name: str = "app",
        *,
        store: str | None = None,
        faucet: int = 50,
        listen: PeerSpec | None = None,
        peers: Mapping[str, PeerSpec] | None = None,
        fabric: FabricNode | None = None,
    ) -> None:
        self.name = name
        self.store = os.path.expanduser(store) if store else None
        self.faucet = faucet
        self._accounts: dict[str, AccountNode] = {}
        self._balances: dict[str, int] = {}     # persisted PLS balances, keyed by external id
        self._fabric = fabric
        self._fabric_runtime: _FabricRuntime | None = None
        if self._fabric is None and (listen is not None or peers):
            bind = _peer(listen or ("127.0.0.1", 0))
            self._fabric = FabricNode(host=bind.host, port=bind.port)
        self.web = self._fabric.web if self._fabric is not None else Web()
        self._records: list[dict] = []           # woven records/edges (for persistence + replay)
        self._term_cid: dict[str, str] = {}
        self._clock = 0
        self._beat = 0
        self._lens: Lens | None = None   # external, read-only interpreter (host-injected)
        if self._fabric is not None:
            self._fabric_runtime = _FabricRuntime(self._fabric)
            for name_, peer in (peers or {}).items():
                self.add_peer(name_, peer)
        if self.store and os.path.exists(self.store):
            self._load()

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    # -- identity + economy ------------------------------------------------
    def actor(self, external_id: str) -> dict:
        """A *stable* knitweb account for an external user id (faucet-seeded once).

        The account is **deterministic from ``external_id``** (via ``AccountNode.from_seed``):
        the same id always yields the same wallet, with no key to store. The flip side is that
        the seed *is* the key — fine for app/bridge/dev identities, but the ``external_id`` must
        be kept secret and must not be used for high-value custody (see the module SECURITY note).
        """
        if external_id not in self._accounts:
            bal = self._balances.get(external_id, self.faucet)
            self._accounts[external_id] = AccountNode.from_seed(external_id, {"PLS": bal})
            self._balances[external_id] = bal
        a = self._accounts[external_id]
        return {"id": external_id, "address": a.address, "pulses": a.balance("PLS")}

    def _node(self, external_id: str) -> AccountNode:
        self.actor(external_id)
        return self._accounts[external_id]

    def balance(self, external_id: str, symbol: str = "PLS") -> int:
        return self._node(external_id).balance(symbol)

    def transfer(self, frm: str, to: str, amount: int, symbol: str = "PLS") -> dict:
        """Move value between two external ids — a real, signed Knit."""
        knit = self._node(frm).transfer_to(self._node(to), symbol, amount, self._tick())
        self._balances[frm] = self._node(frm).balance("PLS")
        self._balances[to] = self._node(to).balance("PLS")
        self._save()
        return {"knit": knit.id, "from": self.balance(frm), "to": self.balance(to)}

    # -- the shared, persistent web ----------------------------------------
    @property
    def fabric_address(self) -> PeerAddress | None:
        """The listening p2p fabric address when this App is p2p-backed."""
        return self._fabric.address if self._fabric is not None else None

    def add_peer(self, name: str, peer: PeerSpec) -> None:
        """Register a p2p fabric peer for future App weaves."""
        if self._fabric is None:
            raise RuntimeError("App was not created with a p2p fabric node")
        self._fabric.add_peer(name, _peer(peer))

    def sync_from(self, peer: PeerSpec) -> int:
        """Pull a peer App/FabricNode's records and rebuild this App's Web view."""
        if self._fabric is None or self._fabric_runtime is None:
            raise RuntimeError("App was not created with a p2p fabric node")
        added = self._fabric_runtime.run(self._fabric.sync_from(_peer(peer)))
        self._refresh_from_web()
        self._save()
        return added

    def close(self) -> None:
        if self._fabric_runtime is not None:
            self._fabric_runtime.close()
            self._fabric_runtime = None

    def __enter__(self) -> "App":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def _weave(self, record: dict, *, publish: bool = True) -> str:
        if publish and self._fabric is not None and self._fabric_runtime is not None:
            return self._fabric_runtime.run(self._fabric.weave(record))
        return self.web.weave(record)

    def _term(self, term: str, *, publish: bool = True) -> str:
        key = term.casefold()
        cid = self._term_cid.get(key)
        if cid is None:
            cid = self._weave({"kind": "term", "term": term}, publish=publish)
            self._term_cid[key] = cid
        return cid

    @staticmethod
    def _link_record(subject: str, obj: str, relation: str, weight: int) -> dict:
        return {
            "kind": "app-link",
            "subject": subject,
            "object": obj,
            "relation": relation,
            "weight": max(1, int(weight)),
        }

    def _apply_link_record(self, record: dict, *, publish_terms: bool = False):
        return self.web.link(
            self._term(str(record["subject"]), publish=publish_terms),
            self._term(str(record["object"]), publish=publish_terms),
            rel=str(record.get("relation", "links")),
            weight=max(1, int(record.get("weight", 1))),
        )

    @staticmethod
    def _record_entry(record: dict) -> dict | None:
        kind = record.get("kind")
        if kind == "term":
            return None
        if kind == "app-link":
            return {
                "t": "link",
                "subject": record["subject"],
                "object": record["object"],
                "relation": record.get("relation", "links"),
                "weight": max(1, int(record.get("weight", 1))),
            }
        return {"t": "record", "data": record}

    def _refresh_from_web(self) -> None:
        """Derive App records and edges from p2p-woven node records."""
        seen = {json.dumps(r, sort_keys=True, separators=(",", ":")) for r in self._records}
        for record in list(self.web.nodes.values()):
            if record.get("kind") == "term":
                term = str(record.get("term", ""))
                if term:
                    self._term_cid.setdefault(term.casefold(), self.web.weave(record))
                continue
            if record.get("kind") == "app-link":
                self._apply_link_record(record, publish_terms=False)
            entry = self._record_entry(record)
            if entry is None:
                continue
            key = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                self._records.append(entry)
                seen.add(key)

    def attest(self, author: str, record: dict) -> dict:
        """Weave an app record (a fact / production / claim) into the shared web."""
        self.actor(author)
        rec = {**record, "by": author}
        cid = self._weave(rec)
        self._records.append({"t": "record", "data": rec})
        self._save()
        n, e = self.web.size
        return {"cid": cid, "nodes": n, "edges": e}

    def link(self, subject: str, obj: str, relation: str = "links", weight: int = 1) -> dict:
        """Knit two terms together — a typed, weighted edge between two nodes."""
        record = self._link_record(subject, obj, relation, weight)
        edge = self._apply_link_record(record, publish_terms=True)
        self._weave(record)
        self._records.append({"t": "link", "subject": subject, "object": obj,
                              "relation": relation, "weight": max(1, int(weight))})
        self._save()
        n, e = self.web.size
        return {"edge": edge.cid, "nodes": n, "edges": e}

    # -- validation + provenance -------------------------------------------
    def validate(self, verdicts) -> dict:
        """Settle peer verdicts with the BFT k-of-n quorum."""
        res = quorum.tally(quorum.Verdict(v) for v in verdicts)
        return {"outcome": res.outcome.value, "confirms": res.confirms,
                "mismatches": res.mismatches, "released": res.releases, "threshold": res.threshold}

    def anchor(self) -> dict:
        """Anchor the current web to OriginTrail — a verifiable UAL + notary receipt."""
        self._refresh_from_web()
        n, e = self.web.size
        if n == 0:
            return {"ual": None, "verified": False, "nodes": 0, "edges": 0}
        self._beat += 1
        beat = Pulse(interval_s=60, genesis_ts=0).beat(timestamp=self._beat, state_root=web_state_root(self.web))
        cp = checkpoint(self.web, beat)
        r = Notary(_NOTARY_PRIV).anchor(cp, OriginTrailAnchorBackend(), self._beat)
        return {"ual": r.external_ref, "state_root": cp.state_root, "verified": bool(r.sig),
                "nodes": n, "edges": e}

    def web_state(self, limit: int = 50) -> dict:
        self._refresh_from_web()
        n, e = self.web.size
        return {"nodes": n, "edges": e, "state_root": web_state_root(self.web),
                "records": self._records[-limit:][::-1]}

    # -- interpretation (external, read-only Lens delegation) --------------
    def set_lens(self, lens: Lens | None) -> None:
        """Register (or clear, with ``None``) an external Lens interpreter.

        A *Lens* is a host-supplied callable ``lens(query, snapshot, params) -> result``
        that reads the Web through a snapshot and returns an interpretation. It lives outside
        Pulse — an LLM / vector / graph-DB interpreter belongs in a separate service or
        package — so Pulse adds **no** dependency for it; this is a pure delegation seam.
        The Lens is only ever handed a *read-only, deep-copied* snapshot (see
        :func:`~knitweb.fabric.snapshot.web_snapshot`), never the live Web, so it cannot
        mutate fabric state regardless of what it does (knitweb/pulse#157). ``params`` is a
        (possibly empty) caller-supplied mapping forwarded verbatim to scope the query.
        Because the Lens is untrusted host code, any exception it raises is contained and
        turned into a deterministic ``interpreter-error`` contract — Pulse keeps serving.
        """
        self._lens = lens

    @property
    def has_lens(self) -> bool:
        """True when an external Lens interpreter is registered."""
        return self._lens is not None

    def interpret(self, query: str, params: Mapping | None = None) -> dict:
        """Delegate read-only interpretation of *query* to the registered Lens.

        This is **strictly read-only**: it weaves/links/mints nothing and only ever
        passes a deterministic deep-copied :func:`~knitweb.fabric.snapshot.web_snapshot`
        to the Lens — there is no write path here whatsoever.

        With **no Lens registered** it returns a deterministic, safe contract response
        ``{"ok": False, "lens": False, "reason": "no-interpreter-installed", ...}`` so
        Pulse keeps serving without any interpreter installed. With a Lens registered it
        returns ``{"ok": True, "lens": True, "query": …, "result": <lens output>}``; if the
        Lens itself raises (the normal failure mode of an LLM / vector / graph-DB backend)
        the error is contained and a deterministic ``{"ok": False, "lens": True,
        "reason": "interpreter-error", ...}`` contract is returned instead — the gateway
        never crashes on host-interpreter faults.
        """
        q = str(query)
        if self._lens is None:
            n, e = self.web.size
            return {"ok": False, "lens": False, "reason": "no-interpreter-installed",
                    "query": q, "nodes": n, "edges": e}
        # Hand the Lens an isolated, read-only snapshot — never the live Web — plus a copy
        # of any caller params. The Lens is untrusted host code: contain ANY exception it
        # raises and return a deterministic error contract rather than dropping the request.
        # The Lens's exception text is deliberately NOT leaked into the response.
        snapshot = web_snapshot(self.web)
        try:
            result = self._lens(q, snapshot, dict(params or {}))
        except Exception:
            return {"ok": False, "lens": True, "reason": "interpreter-error", "query": q}
        return {"ok": True, "lens": True, "query": q, "result": result}

    # -- persistence (replay records, restore balances) --------------------
    def save(self) -> None:
        self._refresh_from_web()
        self._save()

    def _save(self) -> None:
        if not self.store:
            return
        os.makedirs(os.path.dirname(self.store) or ".", exist_ok=True)
        with open(self.store, "w", encoding="utf-8") as fh:
            json.dump({"name": self.name, "balances": self._balances, "records": self._records},
                      fh, indent=2, ensure_ascii=False)

    def _load(self) -> None:
        with open(self.store, encoding="utf-8") as fh:
            d = json.load(fh)
        self._balances = d.get("balances", {})
        for r in d.get("records", []):
            if r["t"] == "link":
                self._apply_link_record(self._link_record(
                    r["subject"],
                    r["object"],
                    r.get("relation", "links"),
                    r.get("weight", 1),
                ))
                self._weave(self._link_record(
                    r["subject"],
                    r["object"],
                    r.get("relation", "links"),
                    r.get("weight", 1),
                ), publish=False)
            else:
                self._weave(r["data"], publish=False)
            self._records.append(r)


def serve(app: App, port: int = 8080, host: str = "127.0.0.1", *, token: str | None = None):
    """Expose an `App` over plain HTTP/JSON so any runtime (FastAPI, Colyseus, Roblox) can drive it.

        GET  /                                  → {ok} (health, always open)
        GET  /balance?id=…                      → {id,address,pulses}
        POST /actor      {id}                   → actor (faucet-seed)
        POST /transfer   {from,to,amount}       → knit + new balances
        POST /attest     {by,record}            → weave a record
        POST /link       {subject,object,relation,weight}
        POST /validate   {verdicts:[...]}       → quorum outcome
        GET  /web                               → web state
        GET  /provenance                        → OriginTrail UAL
        POST /interpret  {query,params?}        → external Lens result (read-only; see below)

    Interpretation (``/interpret``)
    -------------------------------
    ``/interpret`` is a **strictly read-only** delegation hook: it forwards the request to
    an external Lens registered with :meth:`App.set_lens` and returns its result. It never
    weaves, links, mints, transfers, or otherwise writes — and Pulse adds **no** LLM /
    vector / graph-DB dependency for it. With no Lens registered it answers ``501`` with a
    deterministic ``{"ok": False, "lens": False, "reason": "no-interpreter-installed", …}``
    body, so Pulse keeps serving and every other endpoint works without a Lens installed.
    See ``docs/LENS_INTERPRET_ENDPOINT.md`` for the full contract.

    Security
    --------
    * ``host`` defaults to ``127.0.0.1`` (loopback only). Pass ``host="0.0.0.0"`` to expose
      the gateway on the LAN/internet — a warning is printed when bound to a non-loopback host.
    * ``token`` (optional): when set, every request must carry ``Authorization: Bearer <token>``
      or ``X-Auth-Token: <token>`` and otherwise gets a ``401``. ``/`` (health) stays open.
      When ``token`` is ``None`` the gateway is unauthenticated (dev only) and logs a notice.
    """
    import hmac
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    _OPEN_PATHS = {"/"}  # always reachable without a token (health)

    class H(BaseHTTPRequestHandler):
        def _s(self, code, obj):
            b = _json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def _authed(self) -> bool:
            """True if the request may proceed (no token configured, open path, or valid token)."""
            if token is None or urlparse(self.path).path in _OPEN_PATHS:
                return True
            bearer = self.headers.get("Authorization", "")
            presented = bearer[7:] if bearer.startswith("Bearer ") else self.headers.get("X-Auth-Token", "")
            if hmac.compare_digest(presented, token):
                return True
            self._s(401, {"error": "unauthorized"})
            return False

        def do_GET(self):
            if not self._authed():
                return None
            p = urlparse(self.path); q = parse_qs(p.query)
            if p.path == "/":
                return self._s(200, {"ok": True, "app": app.name})
            if p.path == "/balance":
                return self._s(200, app.actor((q.get("id") or [""])[0]))
            if p.path == "/web":
                return self._s(200, app.web_state())
            if p.path == "/provenance":
                return self._s(200, app.anchor())
            return self._s(404, {"error": "not found"})

        def do_POST(self):
            if not self._authed():
                return None
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                d = _json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/actor":
                    return self._s(200, app.actor(d["id"]))
                if self.path == "/transfer":
                    return self._s(200, app.transfer(d["from"], d["to"], int(d["amount"])))
                if self.path == "/attest":
                    return self._s(200, app.attest(d["by"], d["record"]))
                if self.path == "/link":
                    return self._s(200, app.link(d["subject"], d["object"],
                                                 d.get("relation", "links"), int(d.get("weight", 1))))
                if self.path == "/validate":
                    return self._s(200, app.validate(d["verdicts"]))
                if self.path == "/interpret":
                    # Strictly read-only: delegate to an external Lens. 501 if none is
                    # installed; 502 if the Lens itself errored (upstream interpreter
                    # fault); 200 on success — always a deterministic JSON contract.
                    out = app.interpret(d["query"], d.get("params"))
                    if not out["lens"]:
                        status = 501
                    elif out["ok"]:
                        status = 200
                    else:
                        status = 502
                    return self._s(status, out)
                return self._s(404, {"error": "not found"})
            except (KeyError, ValueError, TypeError) as e:
                return self._s(400, {"error": str(e)})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), H)
    if host not in ("127.0.0.1", "::1", "localhost"):
        print(f"knitweb.gateway WARNING: bound to non-loopback host {host!r} — "
              f"the gateway is reachable from the LAN/internet.")
    if token is None:
        print("knitweb.gateway: unauthenticated (no token set) — dev only.")
    print(f"knitweb.gateway '{app.name}' on http://{host}:{port}")
    srv.serve_forever()
