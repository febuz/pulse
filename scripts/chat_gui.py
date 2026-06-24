"""knitweb.chat — an X.com-style GUI for chat and general LLM questions, woven into the fabric.

A tiny single-file web app that turns the knitweb `App` gateway into a familiar
*timeline* experience: you compose a question ("a Pulse"), a **spider** answers it,
the question + answer are woven into the shared `Web`, and the work is metered in
**PLS** — a real signed `Knit` from the asker to the answering spider (proof that
useful compute was paid for).

Design choices that keep it true to the project:

  * **No new core dependency.** The server is plain ``http.server`` (same stance as
    ``knitweb.gateway.serve``), not FastAPI — the credibly-neutral core stays
    dependency-free. The LLM backend is an *optional* `Lens` that is imported lazily
    and only when ``ANTHROPIC_API_KEY`` is set.
  * **The LLM lives outside Pulse.** Interpretation goes through the read-only
    `App.set_lens` / `App.interpret` seam (see ``docs/LENS_INTERPRET_ENDPOINT.md``).
    Pulse adds no LLM dependency; the `Lens` is host code. With no key configured the
    app still works end to end with a deterministic, honest fallback answer.
  * **Every turn is accounted for.** Asking costs ``ask_cost`` PLS, moved by a real
    `Knit` to the spider account, and the Q&A is woven as a ``chat`` record so the
    timeline is just a view over the fabric.

Run (stdlib only — works with no API key, answers via the fallback lens):

    PYTHONPATH=src python3 scripts/chat_gui.py --port 8090
    # open http://127.0.0.1:8090

Enable real Claude answers:

    export ANTHROPIC_API_KEY=sk-ant-...
    PYTHONPATH=src python3 scripts/chat_gui.py --port 8090

SECURITY: binds 127.0.0.1 by default (loopback only). Pass ``--host 0.0.0.0`` to
expose it (a warning is printed) and ``--token`` to require a bearer token on the
JSON API. See ``knitweb.gateway.serve`` for the same posture.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping

from knitweb.gateway import App


# ── The LLM Lens (host code; lives outside Pulse) ───────────────────────────────

class KnitwebChatLens:
    """A read-only `Lens` that answers a general question, optionally via Claude.

    Called as ``lens(query, snapshot, params)`` by ``App.interpret``. It never writes
    to the fabric — it only reads ``params`` (caller-supplied grounding) and returns a
    plain dict ``{"answer", "model", "grounded"}``.

    Backend selection is lazy and contained: if ``ANTHROPIC_API_KEY`` is set *and* the
    ``anthropic`` SDK imports, questions are answered by Claude (``claude-opus-4-8`` with
    adaptive thinking). Otherwise — or on any backend error — a deterministic, honest
    fallback answer is returned so the app keeps serving with zero external setup.
    """

    SYSTEM = (
        "You are a spider on Knitweb, a peer-to-peer web for verifiable compute and a "
        "traceable knowledge fabric. A user has paid PLS (the activity unit) for you to "
        "answer their question. Answer helpfully and concisely in plain language. You may "
        "note when prior woven context is relevant, but never invent fabric state."
    )

    def __init__(self, *, model: str = "claude-opus-4-8", max_tokens: int = 4096) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = None
        self._tried = False

    def _client_or_none(self):
        """Lazily build an Anthropic client; return None if unavailable (no key / no SDK)."""
        if self._tried:
            return self._client
        self._tried = True
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        try:
            import anthropic  # imported only when a key is present
            self._client = anthropic.Anthropic()
        except Exception:
            self._client = None
        return self._client

    def __call__(self, query: str, snapshot: Mapping, params: Mapping) -> dict:
        grounded = int((params or {}).get("grounded", 0))
        client = self._client_or_none()
        if client is None:
            return {
                "answer": self._fallback(query, grounded),
                "model": "knitweb-fallback",
                "grounded": grounded,
            }
        # Real Claude answer. Non-streaming with a bounded max_tokens stays well under
        # the SDK's HTTP-timeout guard; adaptive thinking is the recommended default.
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},
            system=self.SYSTEM,
            messages=[{"role": "user", "content": str(query)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return {"answer": text.strip() or "(empty answer)", "model": resp.model, "grounded": grounded}

    @staticmethod
    def _fallback(query: str, grounded: int) -> str:
        related = (
            f" There are {grounded} related pulses already hanging in the web."
            if grounded else " It is the first of its kind in the web so far."
        )
        return (
            "⚡ This spider has no LLM backend wired up yet — set ANTHROPIC_API_KEY to let "
            "Claude answer. Your question was still metered in PLS and woven into the shared "
            "fabric, so the accounting and provenance work end to end." + related
        )


# ── The chat service (HTTP-free, fully testable) ────────────────────────────────

class InsufficientPulses(Exception):
    """Raised when an asker cannot cover the ask cost."""


class ChatService:
    """Identity + PLS metering + fabric-woven timeline on top of the knitweb `App`.

    All the behaviour lives here (no HTTP), so it is exercised directly in tests. The
    HTTP layer below is a thin adapter over these methods.
    """

    def __init__(
        self,
        app: App | None = None,
        *,
        spider: str = "spider:opus",
        ask_cost: int = 1,
        lens: KnitwebChatLens | None = None,
    ) -> None:
        self.app = app or App("knitweb-chat")
        self.spider = spider
        self.ask_cost = max(0, int(ask_cost))
        self.lens = lens or KnitwebChatLens()
        self.app.set_lens(self.lens)
        self.app.actor(self.spider)  # seed the answering spider account

    # -- reads ------------------------------------------------------------------
    def me(self, user: str) -> dict:
        return self.app.actor(user)

    def _chat_records(self) -> list[dict]:
        state = self.app.web_state(limit=10_000)
        out = []
        for entry in state["records"]:
            if entry.get("t") == "record" and entry.get("data", {}).get("kind") == "chat":
                out.append(entry["data"])
        return out

    def feed(self, limit: int = 50) -> list[dict]:
        # web_state already returns most-recent-first; keep that order.
        return self._chat_records()[: max(1, int(limit))]

    def stats(self) -> dict:
        state = self.app.web_state(limit=10_000)
        return {
            "nodes": state["nodes"],
            "edges": state["edges"],
            "pulses": len(self._chat_records()),
            "lens": "claude" if os.getenv("ANTHROPIC_API_KEY") else "fallback",
        }

    # -- the one write: ask a question -----------------------------------------
    def ask(self, user: str, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty question")
        if len(text) > 4000:
            raise ValueError("question too long (max 4000 chars)")

        self.app.actor(user)
        if self.app.balance(user) < self.ask_cost:
            raise InsufficientPulses(
                f"need {self.ask_cost} PLS, have {self.app.balance(user)}"
            )

        grounded = len(self._chat_records())
        out = self.app.interpret(text, {"by": user, "grounded": grounded})
        result = out.get("result") if out.get("ok") else None
        if not isinstance(result, dict):
            result = {"answer": KnitwebChatLens._fallback(text, grounded),
                      "model": "knitweb-fallback", "grounded": grounded}
        answer = str(result.get("answer", "")).strip() or "(no answer)"
        model = str(result.get("model", "knitweb-fallback"))

        # Pay the spider for the useful work — a real signed Knit.
        if self.ask_cost:
            self.app.transfer(user, self.spider, self.ask_cost)

        woven = self.app.attest(user, {
            "kind": "chat",
            "text": text,
            "answer": answer,
            "model": model,
            "spider": self.spider,
            "cost": self.ask_cost,
        })

        return {
            "by": user,
            "text": text,
            "answer": answer,
            "model": model,
            "spider": self.spider,
            "cost": self.ask_cost,
            "cid": woven["cid"],
            "pulses": self.app.balance(user),
            "spider_pulses": self.app.balance(self.spider),
            "nodes": woven["nodes"],
            "edges": woven["edges"],
        }


# ── The X.com-style single-page UI ──────────────────────────────────────────────

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Knitweb · Pulse</title>
<style>
:root{--bg:#000;--panel:#16181c;--line:#2f3336;--fg:#e7e9ea;--dim:#71767b;
  --accent:#1d9bf0;--accent2:#0c7abf;--g:#00ba7c;--amber:#ffd400}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}
.app{display:grid;grid-template-columns:230px minmax(0,600px) 320px;gap:0;max-width:1180px;margin:0 auto;min-height:100vh}
@media(max-width:1000px){.app{grid-template-columns:68px minmax(0,1fr)}.right{display:none}}
@media(max-width:600px){.app{grid-template-columns:1fr}.left .label{display:none}}
/* left nav */
.left{border-right:1px solid var(--line);padding:8px 12px;position:sticky;top:0;height:100vh}
.brand{font-size:26px;font-weight:800;color:var(--accent);padding:10px 12px}
.nav a{display:flex;align-items:center;gap:14px;padding:11px 12px;border-radius:999px;font-size:19px;color:var(--fg)}
.nav a:hover{background:var(--panel)}
.nav .ico{width:24px;text-align:center}
.pulsebtn{display:block;width:100%;margin:14px 0;background:var(--accent);color:#fff;border:none;
  border-radius:999px;padding:14px;font-size:16px;font-weight:700;cursor:pointer}
.pulsebtn:hover{background:var(--accent2)}
/* center */
.center{border-right:1px solid var(--line);min-width:0}
.hd{position:sticky;top:0;background:rgba(0,0,0,.65);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--line);padding:14px 16px;font-size:20px;font-weight:800;z-index:5}
.compose{display:flex;gap:12px;padding:12px 16px;border-bottom:10px solid var(--panel)}
.avatar{width:42px;height:42px;border-radius:999px;flex:0 0 42px;
  background:linear-gradient(135deg,var(--accent),var(--g));display:flex;align-items:center;
  justify-content:center;font-weight:800;color:#fff}
.compose .body{flex:1;min-width:0}
.compose textarea{width:100%;background:transparent;border:none;color:var(--fg);
  font-size:19px;resize:none;outline:none;min-height:54px}
.compose .row{display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid var(--line);padding-top:10px;margin-top:6px}
.compose .ask{background:var(--accent);color:#fff;border:none;border-radius:999px;
  padding:9px 20px;font-weight:700;cursor:pointer}
.compose .ask:disabled{opacity:.5;cursor:default}
.hint{color:var(--dim);font-size:13px}
.post{display:flex;gap:12px;padding:14px 16px;border-bottom:1px solid var(--line)}
.post .body{flex:1;min-width:0}
.post .who{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.post .handle{font-weight:700}.post .at{color:var(--dim)}
.q{margin:2px 0 10px;white-space:pre-wrap;word-wrap:break-word}
.answer{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:12px 14px;
  white-space:pre-wrap;word-wrap:break-word}
.answer .spider{display:flex;gap:7px;align-items:center;font-size:13px;color:var(--dim);margin-bottom:7px}
.dot{width:18px;height:18px;border-radius:999px;background:linear-gradient(135deg,var(--g),var(--accent));flex:0 0 18px}
.meta{display:flex;gap:16px;color:var(--dim);font-size:13px;margin-top:10px;flex-wrap:wrap}
.badge{border:1px solid var(--line);border-radius:999px;padding:1px 9px;font-size:12px}
.amber{color:var(--amber)}.green{color:var(--g)}
.cid{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--dim)}
.empty{padding:40px 16px;color:var(--dim);text-align:center}
/* right */
.right{padding:12px;position:sticky;top:0;height:100vh;overflow:auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:16px}
.card h3{font-size:19px;margin-bottom:12px}
.kv{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)}
.kv:last-child{border:none}.kv .k{color:var(--dim)}.kv .v{font-weight:700}
.big{font-size:30px;font-weight:800;color:var(--g)}
.idrow{display:flex;gap:8px;margin-top:8px}
.idrow input{flex:1;min-width:0;background:#000;border:1px solid var(--line);color:var(--fg);
  border-radius:8px;padding:8px}
.idrow button{background:transparent;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px 10px;cursor:pointer}
.muted{color:var(--dim);font-size:13px;line-height:1.6}
.err{color:#f4212e;font-size:13px;padding:4px 16px}
</style>
</head>
<body>
<div class="app">

  <nav class="left">
    <div class="brand">✷</div>
    <div class="nav">
      <a href="#"><span class="ico">🏠</span><span class="label">Home</span></a>
      <a href="#"><span class="ico">⚡</span><span class="label">Pulses</span></a>
      <a href="#"><span class="ico">🕸️</span><span class="label">Fabric</span></a>
      <a href="#"><span class="ico">💰</span><span class="label">Wallet</span></a>
    </div>
    <button class="pulsebtn" onclick="document.getElementById('q').focus()">Pulse</button>
  </nav>

  <main class="center">
    <div class="hd">Home</div>
    <div class="compose">
      <div class="avatar" id="myav">?</div>
      <div class="body">
        <textarea id="q" placeholder="What do you want to know?" maxlength="4000"></textarea>
        <div class="row">
          <span class="hint">Costs <b id="cost">1</b> PLS · answered by a spider · woven into the fabric</span>
          <button class="ask" id="ask">Pulse</button>
        </div>
      </div>
    </div>
    <div class="err" id="err"></div>
    <div id="feed"><div class="empty">Loading the web…</div></div>
  </main>

  <aside class="right">
    <div class="card">
      <h3>Wallet</h3>
      <div class="big"><span id="bal">—</span> <span style="font-size:15px;color:var(--dim)">PLS</span></div>
      <div class="kv"><span class="k">handle</span><span class="v" id="hdl">—</span></div>
      <div class="kv"><span class="k">address</span><span class="v cid" id="addr">—</span></div>
      <div class="idrow">
        <input id="who" placeholder="your handle">
        <button onclick="setHandle()">Switch</button>
      </div>
    </div>
    <div class="card">
      <h3>Fabric</h3>
      <div class="kv"><span class="k">pulses woven</span><span class="v" id="s-pulses">—</span></div>
      <div class="kv"><span class="k">nodes</span><span class="v" id="s-nodes">—</span></div>
      <div class="kv"><span class="k">edges</span><span class="v" id="s-edges">—</span></div>
      <div class="kv"><span class="k">answer engine</span><span class="v" id="s-lens">—</span></div>
    </div>
    <div class="card">
      <h3>How it works</h3>
      <p class="muted">Every question is metered in <b>PLS</b> and paid to the spider that
      answers it with a real signed Knit. The question and answer are woven into the shared
      Web, so this timeline is just a view over the verifiable fabric.</p>
    </div>
  </aside>
</div>

<script>
const $ = id => document.getElementById(id);
let HANDLE = localStorage.getItem('kw_handle') || ('spider_' + Math.random().toString(36).slice(2,7));
localStorage.setItem('kw_handle', HANDLE);

function initials(s){return (s||'?').replace(/[^a-z0-9]/ig,'').slice(0,2).toUpperCase() || '?';}
async function jget(u){const r=await fetch(u);return r.json();}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
  const d=await r.json(); if(!r.ok) throw new Error(d.error||('HTTP '+r.status)); return d;}

function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}

function renderPost(p){
  return `<div class="post">
    <div class="avatar">${initials(p.by)}</div>
    <div class="body">
      <div class="who"><span class="handle">${esc(p.by)}</span><span class="at">@${esc(p.by)}</span></div>
      <div class="q">${esc(p.text)}</div>
      <div class="answer">
        <div class="spider"><span class="dot"></span> ${esc(p.spider||'spider')} · answered ·
          <span class="badge">${esc(p.model||'')}</span></div>
        ${esc(p.answer)}
      </div>
      <div class="meta">
        <span class="amber">⚡ ${p.cost} PLS</span>
        <span class="cid">${esc((p.cid||'').slice(0,22))}…</span>
      </div>
    </div>
  </div>`;
}

async function refresh(){
  const me = await jget('/api/me?id='+encodeURIComponent(HANDLE));
  $('bal').textContent = me.pulses;
  $('hdl').textContent = HANDLE;
  $('addr').textContent = (me.address||'').slice(0,10)+'…';
  $('myav').textContent = initials(HANDLE);
  $('who').value = HANDLE;
  const st = await jget('/api/stats');
  $('s-pulses').textContent = st.pulses;
  $('s-nodes').textContent = st.nodes;
  $('s-edges').textContent = st.edges;
  $('s-lens').textContent = st.lens;
  $('cost').textContent = st.ask_cost ?? 1;
  const feed = await jget('/api/feed?limit=50');
  $('feed').innerHTML = feed.length ? feed.map(renderPost).join('')
    : '<div class="empty">No pulses yet. Ask the first question ✷</div>';
}

async function ask(){
  const t = $('q').value.trim();
  if(!t) return;
  $('err').textContent=''; $('ask').disabled=true; $('ask').textContent='Pulsing…';
  try{
    await jpost('/api/ask',{id:HANDLE,text:t});
    $('q').value='';
    await refresh();
  }catch(e){ $('err').textContent = e.message; }
  finally{ $('ask').disabled=false; $('ask').textContent='Pulse'; }
}

function setHandle(){
  const v=$('who').value.trim(); if(!v) return;
  HANDLE=v; localStorage.setItem('kw_handle',v); refresh();
}

$('ask').onclick=ask;
$('q').addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter')ask();});
refresh();
setInterval(refresh, 8000);
</script>
</body>
</html>"""


# ── stdlib HTTP server (mirrors knitweb.gateway.serve posture) ──────────────────

def serve_chat(service: ChatService, *, port: int = 8090, host: str = "127.0.0.1",
               token: str | None = None):
    """Expose `service` as an X.com-style web app over plain HTTP (stdlib only).

        GET  /                     → the SPA (always open)
        GET  /api/me?id=…          → {id,address,pulses}
        GET  /api/stats            → {nodes,edges,pulses,lens,ask_cost}
        GET  /api/feed?limit=…     → [chat records, newest first]
        POST /api/ask {id,text}    → woven Q&A + new balances

    ``host`` defaults to loopback; pass ``token`` to require a bearer token on every
    path except ``/`` (the SPA stays open so the page can load and then authenticate).
    """
    import hmac
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    _OPEN = {"/"}

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj, ctype="application/json"):
            if ctype == "application/json":
                body = json.dumps(obj, ensure_ascii=False).encode()
            else:
                body = obj.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            if token is None or urlparse(self.path).path in _OPEN:
                return True
            bearer = self.headers.get("Authorization", "")
            presented = bearer[7:] if bearer.startswith("Bearer ") else self.headers.get("X-Auth-Token", "")
            if hmac.compare_digest(presented, token):
                return True
            self._send(401, {"error": "unauthorized"})
            return False

        def do_GET(self):
            if not self._authed():
                return None
            p = urlparse(self.path)
            q = parse_qs(p.query)
            if p.path == "/":
                return self._send(200, _HTML, "text/html; charset=utf-8")
            if p.path == "/api/me":
                return self._send(200, service.me((q.get("id") or [""])[0] or "anon"))
            if p.path == "/api/stats":
                return self._send(200, {**service.stats(), "ask_cost": service.ask_cost})
            if p.path == "/api/feed":
                limit = int((q.get("limit") or ["50"])[0])
                return self._send(200, service.feed(limit))
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authed():
                return None
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/ask":
                    return self._send(200, service.ask(d.get("id") or "anon", d.get("text", "")))
                return self._send(404, {"error": "not found"})
            except InsufficientPulses as e:
                return self._send(402, {"error": str(e)})
            except (KeyError, ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), H)
    if host not in ("127.0.0.1", "::1", "localhost"):
        print(f"knitweb.chat WARNING: bound to non-loopback host {host!r} — reachable from the LAN/internet.")
    if token is None:
        print("knitweb.chat: unauthenticated JSON API (no token set) — dev only.")
    engine = "claude" if os.getenv("ANTHROPIC_API_KEY") else "fallback (set ANTHROPIC_API_KEY for Claude)"
    print(f"knitweb.chat on http://{host}:{port} · answer engine: {engine}")
    srv.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Knitweb chat — an X.com-style GUI for general LLM questions.")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token", default=None, help="require this bearer token on the JSON API")
    ap.add_argument("--store", default=None, help="persist the fabric/balances to this JSON path")
    ap.add_argument("--spider", default="spider:opus", help="account that answers and earns PLS")
    ap.add_argument("--ask-cost", type=int, default=1, help="PLS charged per question")
    args = ap.parse_args(argv)

    app = App("knitweb-chat", store=args.store)
    service = ChatService(app, spider=args.spider, ask_cost=args.ask_cost)
    serve_chat(service, port=args.port, host=args.host, token=args.token)


if __name__ == "__main__":
    main()
