# Features

This file tracks user-facing and operator-facing capabilities. Requirements and
constraints live in [REQUIREMENTS.md](REQUIREMENTS.md).

## Implemented

- Pure-Python `knitweb` package installable from this repository.
- `knitweb` / `pulse` CLI entry points for wallet, identity, host, page, peer,
  node, payment, bytecode compile, bundle verification, and edge-load workflows.
- Account ledger with integer PLS balances, signed Knits, Fibers, Braids, and
  replay protection by network id.
- Canonical CBOR content addressing with CIDv1 and strict float rejection.
- Local persisted wallet snapshots with atomic writes.
- Stdlib-asyncio P2P node for signed feed sync and two-party PLS payment.
- Fabric `Web` primitives for signed records, checkpoints, and provenance.
- Proof-of-Useful-Work primitives for quorum, sampling, verification, dispute,
  collateral, and scheduler flows.
- Domain looms for chemistry, finance, operational, and supply-chain examples.
- OriginTrail-compatible anchoring abstractions and local proof receipts.
- Molgang-compatible CLI JSON contract:
  - `pulse identity create --json`
  - `pulse host status --json`
  - `pulse page publish --json`
  - `pulse peer status --json`

## Planned

- Long-running host service with peer discovery, relay, and provider operations.
- Browser/user-agent integration on top of the same Python protocol core.
- Stable package publishing once the API contracts stop moving quickly.
- Production key custody instead of clear-text local development wallets.
- Provider accounting reports for PLS earned, spent, escrowed, and slashed.
