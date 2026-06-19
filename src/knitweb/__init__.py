"""Knitweb — a peer-to-peer crypto web with native token PLS (Pulse).

Knitweb is a credibly-neutral DePIN where peer-to-peer web-workers ("spiders")
sell verifiable GPU compute and weave a knowledge + resource fabric. Users pay in
**PLS** ("pulses") for activity on the web — not for fibers or knits themselves.
Value unit: a "fiber" (Dutch *vezel*) carries data; a *pulse* is the metered unit
of activity you pay for. (FBR is reserved — it may become a separate regional
token later; the native pay-token is PLS.)

Layered architecture:
  L0  core      — secp256k1 ECDSA + SHA-256, canonical CBOR, content addressing
  L1  ledger    — blob / fiber / knitweb / knit / braid / node (integer PLS balances)
  L2  p2p       — asyncio signed-feed sync + static peers (DHT backend later)
  L3  fabric    — Web (woven global graph) + items + agent/scorer/masterdata
  L4  pouw      — proof-of-useful-work (Julia + WebGPU), sampled re-execution
  L5  knitwebs  — finance / operational / supply-chain / chemistry plugins
  L6  token     — PLS access/pay-token + demand-gated, bounded minting (`token.mint`) + anchors

Core modules (the seven primitives): Blob, Fiber, Knitweb, Knit, Braid, Web, Pulse.
"""

# Single-sourced version: pyproject's [project].version is the canonical value.
# When installed, importlib.metadata reflects the real distribution version; when
# running straight from the source tree (no installed dist) we fall back to the
# static literal below, which MUST equal pyproject's version (a property test in
# tests/property/test_tools_cli.py asserts byte-equality so they cannot drift).
_VERSION_FALLBACK = "0.6.0"

try:  # pragma: no cover - trivial import-time branch
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("knitweb")
    except PackageNotFoundError:
        __version__ = _VERSION_FALLBACK
except ImportError:  # pragma: no cover - importlib.metadata is stdlib on 3.12+
    __version__ = _VERSION_FALLBACK

TOKEN = "PLS"        # native pay-token: "pulses". FBR reserved for later/regional use.
