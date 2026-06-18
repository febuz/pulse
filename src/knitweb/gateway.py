"""knitweb.gateway — the turnkey **app layer** for building on the knitweb.

Building a real app on knitweb's primitives (MOLGANG was the proving ground) surfaces the
same gaps *every* builder hits — the low-level layers are deliberately minimal, but an app
shouldn't have to re-solve all of this:

  1. **Identity from an external id.** Apps have their own users (a Roblox UserId, a
     username). There was no way to get a *stable* knitweb account for one without managing
     keys → `App.actor(external_id)` (built on `AccountNode.from_seed`).
  2. **A persistent, shared web.** The fabric `Web` is in-memory with no save/load, so the
     woven knowledge vanishes on restart and can't be shared between processes → `App` persists
     every woven record/edge and rebuilds the `Web` on load (and `store=` makes two instances
     share one web).
  3. **Turnkey economy / validation / provenance.** Faucet, balances, transfers, BFT-quorum
     validation, and OriginTrail anchoring all existed as separate primitives that each app
     re-wired → `App` exposes them as one intuitive object.
  4. **Any runtime.** `serve(app)` exposes the whole thing over plain HTTP/JSON so a FastAPI,
     a Colyseus server, or a Roblox HttpService can drive it — not just Python.

`App` is the answer: identity + economy + a persistent shared web + validation + provenance,
in one object, in ~a dozen intuitive methods.
"""

from __future__ import annotations

import json
import os

from .anchor import Notary
from .anchor.origintrail import OriginTrailAnchorBackend
from .core.pulse import Pulse
from .fabric.items import checkpoint, web_state_root
from .fabric.web import Web
from .ledger.node import AccountNode
from .pouw import quorum

_NOTARY_PRIV = "0" * 63 + "1"  # fixed dev notary → reproducible UAL per web state


class App:
    """A knitweb application: stable identities, a token economy, a persistent shared web,
    peer validation and provenance — all wired for you."""

    def __init__(self, name: str = "app", *, store: str | None = None, faucet: int = 50) -> None:
        self.name = name
        self.store = os.path.expanduser(store) if store else None
        self.faucet = faucet
        self._accounts: dict[str, AccountNode] = {}
        self._balances: dict[str, int] = {}     # persisted PLS balances, keyed by external id
        self.web = Web()
        self._records: list[dict] = []           # woven records/edges (for persistence + replay)
        self._term_cid: dict[str, str] = {}
        self._clock = 0
        self._beat = 0
        if self.store and os.path.exists(self.store):
            self._load()

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    # -- identity + economy ------------------------------------------------
    def actor(self, external_id: str) -> dict:
        """A *stable* knitweb account for an external user id (faucet-seeded once)."""
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
    def _term(self, term: str) -> str:
        key = term.casefold()
        cid = self._term_cid.get(key)
        if cid is None:
            cid = self.web.weave({"kind": "term", "term": term})
            self._term_cid[key] = cid
        return cid

    def attest(self, author: str, record: dict) -> dict:
        """Weave an app record (a fact / production / claim) into the shared web."""
        self.actor(author)
        rec = {**record, "by": author}
        cid = self.web.weave(rec)
        self._records.append({"t": "record", "data": rec})
        self._save()
        n, e = self.web.size
        return {"cid": cid, "nodes": n, "edges": e}

    def link(self, subject: str, obj: str, relation: str = "links", weight: int = 1) -> dict:
        """Knit two terms together — a typed, weighted edge between two nodes."""
        edge = self.web.link(self._term(subject), self._term(obj), rel=relation, weight=max(1, weight))
        self._records.append({"t": "link", "subject": subject, "object": obj,
                              "relation": relation, "weight": weight})
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
        n, e = self.web.size
        return {"nodes": n, "edges": e, "state_root": web_state_root(self.web),
                "records": self._records[-limit:][::-1]}

    # -- persistence (replay records, restore balances) --------------------
    def save(self) -> None:
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
                self.web.link(self._term(r["subject"]), self._term(r["object"]),
                              rel=r.get("relation", "links"), weight=max(1, r.get("weight", 1)))
            else:
                self.web.weave(r["data"])
            self._records.append(r)


def serve(app: App, port: int = 8080, host: str = "0.0.0.0"):
    """Expose an `App` over plain HTTP/JSON so any runtime (FastAPI, Colyseus, Roblox) can drive it.

        GET  /balance?id=…                      → {id,address,pulses}
        POST /actor      {id}                   → actor (faucet-seed)
        POST /transfer   {from,to,amount}       → knit + new balances
        POST /attest     {by,record}            → weave a record
        POST /link       {subject,object,relation,weight}
        POST /validate   {verdicts:[...]}       → quorum outcome
        GET  /web                               → web state
        GET  /provenance                        → OriginTrail UAL
    """
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    class H(BaseHTTPRequestHandler):
        def _s(self, code, obj):
            b = _json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def do_GET(self):  # noqa: N802
            p = urlparse(self.path); q = parse_qs(p.query)
            if p.path == "/balance":
                return self._s(200, app.actor((q.get("id") or [""])[0]))
            if p.path == "/web":
                return self._s(200, app.web_state())
            if p.path == "/provenance":
                return self._s(200, app.anchor())
            return self._s(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
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
                return self._s(404, {"error": "not found"})
            except (KeyError, ValueError, TypeError) as e:
                return self._s(400, {"error": str(e)})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), H)
    print(f"knitweb.gateway '{app.name}' on http://localhost:{port}")
    srv.serve_forever()
