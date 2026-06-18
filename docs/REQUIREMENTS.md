# Requirements

This file tracks constraints that code and PRs must satisfy. Features live in
[FEATURES.md](FEATURES.md).

## Runtime Requirements

- Core protocol and CLI code must be pure Python.
- Supported runtime is Python 3.12 or newer.
- The base install may depend on `cryptography` for secp256k1 and SHA-256.
- Hash-critical encoding must stay in the repository and must reject floats.
- Money amounts must be integers only.
- The base package must not require Node.js, npm, a browser build, or a database.
- Optional heavier roles must stay behind extras such as `p2p`, `compute`, or
  `data`.

## CLI Requirements

- The package must expose both `knitweb` and `pulse` console scripts.
- `pulse identity create --json` must create or reuse a real persisted wallet.
- CLI JSON output must never include private key material.
- `pulse host status --json` must report address, identity path, listen address,
  PLS balance, and local page count.
- CLI commands used by Molgang must be subprocess-safe and deterministic enough
  for tests.

## Protocol Requirements

- Canonical serialization must be deterministic across clients.
- Ledger state must be replay-protected by network id.
- P2P message handling must reject malformed or conflicting signed state.
- Useful-work rewards must be auditable: reward, escrow, refund, slash, and
  dispute outcomes need explicit records.
- Any bridge to games or external apps must use public CLI/API contracts, not
  private module internals, unless both repositories are changed in the same PR
  stack.

## Repository Requirements

- CI must install and test the Python package; stale Node-only checks are not
  sufficient for this repository.
- Tests must cover compatibility contracts before Molgang or other apps depend
  on them.
- Documentation must keep feature descriptions separate from requirements and
  constraints.
- Legacy JavaScript assets may remain only as archived references, not as the
  active implementation path.
