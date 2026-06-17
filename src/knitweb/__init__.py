"""Knitweb — a peer-to-peer crypto web with native token PLS (Pulse).

Knitweb is a credibly-neutral DePIN where peer-to-peer web-workers ("spiders")
sell verifiable GPU compute and weave a knowledge + resource fabric. Users pay in
**PLS** ("pulses") for activity on the web — not for fibers or knits themselves.
Value unit: a "fiber" (Dutch *vezel*) carries data; a *pulse* is the metered unit
of activity you pay for. (FBR is reserved — it may become a separate regional
token later; the native pay-token is PLS.)

Layered architecture:
  L0  core      — secp256k1 ECDSA + SHA-256, canonical CBOR, content addressing
  L1  ledger    — blob / fiber / loom / knit / braid / node (integer PLS balances)
  L2  p2p       — py-libp2p signed append-only feeds + DHT
  L3  fabric    — Web (woven global graph) + items + agent/scorer/masterdata
  L4  pouw      — proof-of-useful-work (Julia + WebGPU), sampled re-execution
  L5  looms     — finance / operational / supply-chain / chemistry plugins
  L6  token     — PLS access token + ERC20-like user-issued LoomTokens + anchors

Core modules (the seven primitives): Blob, Fiber, Loom, Knit, Braid, Web, Pulse.
"""

__version__ = "0.0.1"
TOKEN = "PLS"        # native pay-token: "pulses". FBR reserved for later/regional use.
