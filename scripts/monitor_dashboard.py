"""Knitweb Monitor Dashboard — live view of node health, EDS traffic, and knit rate.

Two modes:
  1. **Node monitor**: connects to a running FabricNode via its metrics HTTP endpoint
     (or a self-hosted relay_server /api/relay/health).
  2. **Relay monitor**: polls the self-hosted relay at /api/relay/health for mailbox
     counts, frame throughput, uptime.

Run:
    PYTHONPATH=src uvicorn scripts.monitor_dashboard:app --port 9000

Then open http://localhost:9000 to see the live dashboard.

Environment variables:
    RELAY_URL   — URL of self-hosted relay  (default: http://localhost:8765)
    NODE_URL    — URL of knitweb node HTTP  (default: http://localhost:8000)
    POLL_MS     — SSE push interval in ms   (default: 2000)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

RELAY_URL  = os.getenv("RELAY_URL", "http://localhost:8765")
NODE_URL   = os.getenv("NODE_URL",  "http://localhost:8000")
POLL_MS    = int(os.getenv("POLL_MS", "2000"))

app = FastAPI(title="Knitweb Monitor", docs_url=None, redoc_url=None)

# ── Lightweight poll helpers ───────────────────────────────────────────────────

def _fetch_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


async def _poll_relay() -> dict:
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: _fetch_json(f"{RELAY_URL}/api/relay/health"))
    if data and data.get("status") == "ok":
        m = data.get("metrics", {})
        return {
            "relay_online": True,
            "relay_uptime_s":      m.get("uptime_s", 0),
            "mailboxes_live":      m.get("mailboxes_live", 0),
            "frames_received":     m.get("frames_received", 0),
            "frames_delivered":    m.get("frames_delivered", 0),
            "frames_dropped":      m.get("frames_dropped", 0),
            "fetches_total":       m.get("fetches_total", 0),
            "fetches_timeout":     m.get("fetches_timeout", 0),
            "mailboxes_reaped":    m.get("mailboxes_reaped", 0),
        }
    return {"relay_online": False}


async def _poll_node() -> dict:
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: _fetch_json(f"{NODE_URL}/metrics"))
    if data:
        return {
            "node_online":        True,
            "records_woven":      data.get("records_woven", 0),
            "broadcasts_sent":    data.get("broadcasts_sent", 0),
            "broadcasts_failed":  data.get("broadcasts_failed", 0),
            "sync_pulls":         data.get("sync_pulls", 0),
            "frames_in":          data.get("frames_in", 0),
            "frames_out":         data.get("frames_out", 0),
            "frames_malformed":   data.get("frames_malformed", 0),
            "frames_oversized":   data.get("frames_oversized", 0),
            "banned_refusals":    data.get("banned_refusals", 0),
            "peers_connected":    data.get("peers_connected", 0),
        }
    return {"node_online": False}


# ── SSE stream ────────────────────────────────────────────────────────────────

async def _metrics_stream() -> AsyncGenerator[str, None]:
    interval = POLL_MS / 1000
    prev_knits = 0
    prev_time  = time.monotonic()
    while True:
        relay, node = await asyncio.gather(_poll_relay(), _poll_node())

        now = time.monotonic()
        dt  = max(now - prev_time, 0.001)
        knits_now  = node.get("records_woven", 0)
        knit_rate  = round((knits_now - prev_knits) / dt, 2)
        prev_knits = knits_now
        prev_time  = now

        payload = {
            "ts":        int(now),
            "relay":     relay,
            "node":      node,
            "knit_rate": knit_rate,   # knits/second
        }
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(interval)


@app.get("/stream")
async def sse_stream():
    return StreamingResponse(
        _metrics_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/metrics")
async def raw_metrics():
    relay, node = await asyncio.gather(_poll_relay(), _poll_node())
    return {"relay": relay, "node": node}


# ── Dashboard HTML ────────────────────────────────────────────────────────────

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Knitweb Monitor</title>
<style>
:root{--bg:#09090f;--bg2:#0f1118;--line:#1c2033;--ok:#17BEBB;--warn:#ffbd2e;--err:#ff5f5f;--dim:#6b7a99;--fg:#e2e8f0;--b:#2E6CF6;--g:#B8F35A}
*{box-sizing:border-box;margin:0}
body{font:13px/1.5 'SF Mono','Cascadia Code',monospace;background:var(--bg);color:var(--fg);padding:20px}
h1{font-size:18px;font-weight:700;color:var(--ok);margin-bottom:4px}
.sub{color:var(--dim);font-size:11px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}
@media(max-width:500px){.grid{grid-template-columns:1fr}}
.card{background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card-title{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);margin-bottom:10px}
.metric{display:flex;justify-content:space-between;align-items:baseline;padding:3px 0;border-bottom:1px solid var(--line)}
.metric:last-child{border-bottom:none}
.metric-name{color:var(--dim);font-size:11px}
.metric-val{font-size:15px;font-weight:700;font-family:'SF Mono','Cascadia Code',monospace;transition:color .3s}
.status-badge{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700}
.online{background:rgba(23,190,187,.15);color:var(--ok);border:1px solid rgba(23,190,187,.3)}
.offline{background:rgba(255,95,95,.1);color:var(--err);border:1px solid rgba(255,95,95,.25)}
.knit-rate{font-size:32px;font-weight:800;color:var(--g);text-align:center;padding:10px 0 4px}
.knit-label{text-align:center;font-size:11px;color:var(--dim)}
.spark{height:40px;width:100%;margin-top:10px}
.bar-wrap{margin-top:8px;height:4px;background:var(--line);border-radius:2px;overflow:hidden}
.bar-fill{height:100%;background:var(--ok);border-radius:2px;transition:width .5s}
.ts{float:right;font-size:10px;color:var(--dim)}
.flash{animation:fl .4s}
@keyframes fl{0%{color:#fff}100%{color:inherit}}
</style>
</head>
<body>
<h1>Knitweb Monitor</h1>
<div class="sub">Live — updates every <span id="poll-ms">2000</span> ms
  <span class="ts" id="ts">—</span>
</div>

<div class="grid">

  <!-- Knit rate card -->
  <div class="card">
    <div class="card-title">Knit rate (records/s)</div>
    <div class="knit-rate" id="knit-rate">—</div>
    <div class="knit-label">records_woven: <span id="records-woven">—</span></div>
    <canvas class="spark" id="spark"></canvas>
  </div>

  <!-- Relay card -->
  <div class="card">
    <div class="card-title">
      Relay
      <span id="relay-badge" class="status-badge offline" style="float:right">● offline</span>
    </div>
    <div class="metric"><span class="metric-name">uptime</span><span class="metric-val" id="r-uptime">—</span></div>
    <div class="metric"><span class="metric-name">mailboxes live</span><span class="metric-val" id="r-mailboxes">—</span></div>
    <div class="metric"><span class="metric-name">frames received</span><span class="metric-val" id="r-recv">—</span></div>
    <div class="metric"><span class="metric-name">frames delivered</span><span class="metric-val" id="r-del">—</span></div>
    <div class="metric"><span class="metric-name">frames dropped</span><span class="metric-val" id="r-drop" style="color:var(--warn)">—</span></div>
    <div class="metric"><span class="metric-name">fetches / timeouts</span><span class="metric-val" id="r-fetches">—</span></div>
  </div>

  <!-- Node card -->
  <div class="card">
    <div class="card-title">
      Node
      <span id="node-badge" class="status-badge offline" style="float:right">● offline</span>
    </div>
    <div class="metric"><span class="metric-name">peers connected</span><span class="metric-val" id="n-peers">—</span></div>
    <div class="metric"><span class="metric-name">sync pulls</span><span class="metric-val" id="n-sync">—</span></div>
    <div class="metric"><span class="metric-name">broadcasts sent/fail</span><span class="metric-val" id="n-bcast">—</span></div>
    <div class="metric"><span class="metric-name">frames in/out</span><span class="metric-val" id="n-frames">—</span></div>
    <div class="metric"><span class="metric-name">malformed / oversized</span><span class="metric-val" id="n-bad" style="color:var(--warn)">—</span></div>
    <div class="metric"><span class="metric-name">banned refusals</span><span class="metric-val" id="n-ban" style="color:var(--err)">—</span></div>
  </div>

  <!-- EDS traffic card (Event Distribution System = broadcast layer) -->
  <div class="card">
    <div class="card-title">EDS traffic (broadcasts)</div>
    <div class="metric"><span class="metric-name">sent</span><span class="metric-val ok" id="eds-sent" style="color:var(--ok)">—</span></div>
    <div class="metric"><span class="metric-name">failed</span><span class="metric-val" id="eds-fail" style="color:var(--err)">—</span></div>
    <div class="metric"><span class="metric-name">fail rate</span><span class="metric-val" id="eds-rate">—</span></div>
    <div class="bar-wrap"><div class="bar-fill" id="eds-bar" style="width:0%"></div></div>
  </div>

  <!-- Wire health card -->
  <div class="card">
    <div class="card-title">Wire health</div>
    <div class="metric"><span class="metric-name">frames_in</span><span class="metric-val" id="w-in">—</span></div>
    <div class="metric"><span class="metric-name">frames_out</span><span class="metric-val" id="w-out">—</span></div>
    <div class="metric"><span class="metric-name">malformed</span><span class="metric-val" id="w-mal" style="color:var(--warn)">—</span></div>
    <div class="metric"><span class="metric-name">oversized</span><span class="metric-val" id="w-big" style="color:var(--warn)">—</span></div>
  </div>

  <!-- Relay mailboxes card -->
  <div class="card">
    <div class="card-title">Relay mailboxes</div>
    <div class="metric"><span class="metric-name">live now</span><span class="metric-val" id="mb-live">—</span></div>
    <div class="metric"><span class="metric-name">reaped total</span><span class="metric-val" id="mb-reaped">—</span></div>
    <div class="metric"><span class="metric-name">fetches total</span><span class="metric-val" id="mb-fetches">—</span></div>
    <div class="metric"><span class="metric-name">timeouts</span><span class="metric-val" id="mb-timeout" style="color:var(--dim)">—</span></div>
  </div>

</div>

<script>
const MAX_SPARK = 60;
const sparkData = [];
const canvas = document.getElementById('spark');
const ctx = canvas.getContext('2d');

function drawSpark() {
  const W = canvas.offsetWidth || 200, H = 40;
  canvas.width = W; canvas.height = H;
  ctx.clearRect(0, 0, W, H);
  if (sparkData.length < 2) return;
  const max = Math.max(...sparkData, 0.01);
  ctx.strokeStyle = '#B8F35A';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  sparkData.forEach((v, i) => {
    const x = (i / (MAX_SPARK - 1)) * W;
    const y = H - (v / max) * (H - 4) - 2;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function set(id, val, flash) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = val;
  if (flash) { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
}

function fmt(n) { return typeof n === 'number' ? n.toLocaleString() : '—'; }
function fmtUptime(s) {
  if (!s) return '—';
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = s%60;
  return h>0 ? `${h}h ${m}m` : m>0 ? `${m}m ${ss}s` : `${ss}s`;
}

const src = new EventSource('/stream');
src.onmessage = function(e) {
  const d = JSON.parse(e.data);
  const r = d.relay || {}, n = d.node || {};

  // Timestamp
  set('ts', new Date(d.ts*1000).toLocaleTimeString());

  // Knit rate
  const kr = d.knit_rate ?? 0;
  set('knit-rate', kr.toFixed(2) + '/s');
  set('records-woven', fmt(n.records_woven));
  sparkData.push(kr);
  if (sparkData.length > MAX_SPARK) sparkData.shift();
  drawSpark();

  // Relay
  const relayBadge = document.getElementById('relay-badge');
  if (r.relay_online) {
    relayBadge.className = 'status-badge online'; relayBadge.textContent = '● online';
    set('r-uptime',    fmtUptime(r.relay_uptime_s));
    set('r-mailboxes', fmt(r.mailboxes_live));
    set('r-recv',      fmt(r.frames_received));
    set('r-del',       fmt(r.frames_delivered));
    set('r-drop',      fmt(r.frames_dropped), r.frames_dropped > 0);
    set('r-fetches',   `${fmt(r.fetches_total)} / ${fmt(r.fetches_timeout)}`);
    set('mb-live',     fmt(r.mailboxes_live));
    set('mb-reaped',   fmt(r.mailboxes_reaped));
    set('mb-fetches',  fmt(r.fetches_total));
    set('mb-timeout',  fmt(r.fetches_timeout));
  } else {
    relayBadge.className = 'status-badge offline'; relayBadge.textContent = '● offline';
  }

  // Node
  const nodeBadge = document.getElementById('node-badge');
  if (n.node_online) {
    nodeBadge.className = 'status-badge online'; nodeBadge.textContent = '● online';
    set('n-peers',  fmt(n.peers_connected));
    set('n-sync',   fmt(n.sync_pulls));
    set('n-bcast',  `${fmt(n.broadcasts_sent)} / ${fmt(n.broadcasts_failed)}`);
    set('n-frames', `${fmt(n.frames_in)} / ${fmt(n.frames_out)}`);
    set('n-bad',    `${fmt(n.frames_malformed)} / ${fmt(n.frames_oversized)}`,
        (n.frames_malformed + n.frames_oversized) > 0);
    set('n-ban',    fmt(n.banned_refusals), n.banned_refusals > 0);
    set('w-in',     fmt(n.frames_in));
    set('w-out',    fmt(n.frames_out));
    set('w-mal',    fmt(n.frames_malformed));
    set('w-big',    fmt(n.frames_oversized));
    // EDS
    const total = (n.broadcasts_sent || 0) + (n.broadcasts_failed || 0);
    const failPct = total > 0 ? Math.round((n.broadcasts_failed / total) * 100) : 0;
    set('eds-sent', fmt(n.broadcasts_sent));
    set('eds-fail', fmt(n.broadcasts_failed));
    set('eds-rate', failPct + '%');
    document.getElementById('eds-bar').style.width = Math.min(failPct, 100) + '%';
  } else {
    nodeBadge.className = 'status-badge offline'; nodeBadge.textContent = '● offline';
  }
};
src.onerror = () => console.warn('SSE disconnected');
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(_HTML)
