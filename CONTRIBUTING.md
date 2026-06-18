# Contributing to Knitweb

Thanks for helping weave the **Knitweb** — a credibly-neutral, pure-Python peer-to-peer
crypto **web** (DePIN). This guide is short on purpose; the non-negotiables below exist to
keep the protocol byte-deterministic and the project welcoming.

> 🌍 **Translations of this project's overview** live in [`docs/i18n/`](docs/i18n/). The
> English documents in the repository root are canonical.

## Quick start

```bash
git clone https://github.com/knitweb/pulse.git && cd pulse
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src python3 -m pytest tests/property -q   # fast core proofs (must be green)
```

Python **3.12+**. Runtime deps are deliberately minimal (`cryptography` only for the core).

## How we work

1. **Open an issue first** for anything non-trivial (use the templates) so we can agree on the approach.
2. **Branch** from `main`; keep PRs small and focused; never push to `main`.
3. **Proofs-first.** Every change ends with a runnable test + a green suite. New behaviour ⇒ new property test.
4. **Open a PR** using the template and fill the checklist. CI + review must pass.

## Non-negotiables (read before you touch the core)

- **Canonical bytes are sacred.** All hashing/signing goes through `knitweb.core.canonical`
  (float-free, deterministic CBOR) + `core.canonical.cid` (CIDv1 dag-cbor sha2-256). **Changing
  it changes every hash and signature.** A change that alters any signed-record `kind`/field/value
  will be rejected.
- **Money & state are integers** (wei-style base units). **No floats** anywhere near hashing,
  balances, or canonical encoding.
- **Crypto = secp256k1 ECDSA + SHA-256** only. No Ed25519/BLAKE2b in the value path.
- **No founder premine.** PLS genesis is `mintable=false`, `premine=0`; mint is demand-gated and bounded.

## Vocabulary (enforced — keeps the project legible)

- The brand/primitive vocabulary is **Web · Knitweb · Knit · Pulse · Fiber**. Workers are **spiders**.
- **Never write "loom" or "looms" — only "knitweb."** The domain plugins are **knitwebs**; the L1
  validation primitive is `Knitweb`. (The old weaving-metaphor name is retired.)
- **Never call it a "network"/"net"** — it is a *web* / *fabric*. The **only** allowed technical use of
  "network" is the hash-critical `network` id field inside a signed `Knit` (EIP-155-style chain id) —
  never rename that.
- **PLS** ("pulses") is the active pay-token; **FBR is reserved and not active**. *Fiber* is the value
  unit / brand coin (the `Fiber` primitive is an immutable account-state commitment, never transferred).

## Layout

`src/knitweb/`: `core` (L0) → `ledger` (L1) → `p2p` (L2) → `fabric` (L3) → `pouw` (L4) →
`knitwebs` (L5 domain plugins) → `token` (L6), plus `anchor` / `synaptic` / `edge` / `sdk` / `app`.
See [`README.md`](README.md) and [`docs/`](docs/).

## Licensing & sign-off

By contributing you agree your work is licensed under **Apache-2.0** (see [`LICENSE`](LICENSE)).
Sign your commits off (`git commit -s`, Developer Certificate of Origin).

## Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind; assume good faith.
