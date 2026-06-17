"""Knitweb — a peer-to-peer crypto network with native token FBR.

Knitweb is a credibly-neutral DePIN where peer-to-peer web-workers ("spiders")
sell verifiable GPU compute and weave a knowledge + resource fabric. The native
token is FBR (value unit: a "fiber" / Dutch *vezel*).

Layered architecture:
  L0  core      — secp256k1 ECDSA + SHA-256, canonical CBOR, content addressing
  L1  ledger    — blob / fiber / loom / knit / braid / node (integer FBR balances)
  L2  p2p       — py-libp2p signed append-only feeds + DHT
  L3  fabric    — Web (woven global graph) + items + agent/scorer/masterdata
  L4  pouw      — proof-of-useful-work (Julia + WebGPU), sampled re-execution
  L5  looms     — finance / operational / supply-chain / chemistry plugins
  L6  token     — FBR access token + ERC20-like user-issued LoomTokens + anchors

Core modules (the seven primitives): Blob, Fiber, Loom, Knit, Braid, Web, Pulse.
"""

__version__ = "0.0.1"
TOKEN = "FBR"
