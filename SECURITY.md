# Security Policy

Knitweb is a value-bearing peer-to-peer crypto **web**. We take security seriously and
welcome coordinated disclosure.

## Reporting a vulnerability

**Do not open a public issue for security problems.** Instead:

1. Preferred: open a **private report via [GitHub Security Advisories](https://github.com/knitweb/pulse/security/advisories/new)** on this repository, or
2. Email **security@knitweb.dev** with details (encrypt if you can; ask for a key).

Please include: affected version/commit, a minimal reproduction, impact, and any suggested fix.

### Our commitment

- **Acknowledge** your report within **72 hours**.
- Provide an initial **assessment within 7 days** and keep you updated.
- Credit you in the advisory (unless you prefer to remain anonymous).
- Practice **coordinated disclosure**: we agree a disclosure date with you; please give us a
  reasonable window before going public.

## Especially in scope (the crown jewels)

- **Canonical encoding & content addressing** — `knitweb.core.canonical` (float-free CBOR) and
  `core.canonical.cid` (CIDv1). Any non-determinism, float leak, or hash/signature ambiguity.
- **Signature & key handling** — secp256k1 ECDSA + SHA-256 in `knitweb.core.crypto`.
- **Ledger integer math** — balance/escrow over/underflow, replay (the `network` id / EIP-155 guard).
- **Token mint** — anything that breaks the demand-gated, bounded, no-premine ("no-infinite-mint") guarantee.
- **Proof-of-Useful-Work** — verifier-quorum bypass, collateral-sizing escape, dispute-window/withdraw races, sampling/committee grinding.
- **P2P** — equivocation, feed-proof forgery, reputation/policing evasion.

## Out of scope

Issues requiring physical access to a user's device; third-party dependencies (report upstream);
volumetric DoS without a protocol-level amplification; theoretical issues without a practical impact.

## Safe harbor

Good-faith research that respects this policy, avoids privacy violations and service disruption, and
does not exploit beyond what is needed to demonstrate the issue will not be pursued by us. Thank you
for keeping the web safe.
