# Personhood Foundation — privacy-preserving sybil resistance for vBank

`knitweb.personhood` is the **foundation layer** (L3.5, between the L3 fabric and the L5
domain knitwebs) that lets vBank — voting **and** crowdfunding — gate actions on "this is
a verified unique EU natural person" **without ever putting identity on the fabric**. It is
built as a foundation, not a bolt-on, because the privacy model cannot be retrofitted: the
fabric is append-only, content-addressed and replicated, so once a linkable identifier is
written it cannot be un-published.

This implements the guardrail already stated in `DOMAIN_KNITWEB_INTERFACE.md` (*"Identity
should be a revocable proof, not replicated PII… use pairwise identifiers"*, *"never put raw
ID data or national ID numbers on the fabric"*).

## What is stored on the fabric

Exactly two record kinds, both integer/bytes-only and both passed through a **deny-by-default
whitelist** (`records.assert_personhood_record_shape`) — any field outside the whitelist is a
hard error, the same teeth `core.canonical` applies to floats. A planted `full_name`/`dob`/
`national_id` cannot survive validation.

- **`personhood-anchor`** — a revocable proof, co-signed by the verifier RP **and** the
  holder's pairwise key. Carries: a hash of the issuer's eIDAS Trusted-List entry +
  `issuer_class`, the `scope`, the `scope_nullifier`, the `pairwise_did`, a validity window,
  a **random `revocation_pointer`** (decoupled from the nullifier), and a `proof_digest`.
  No name, no date of birth (only `age_over_18` is proven at admission, off-fabric, and never
  stored), no national identifier.
- **`personhood-revoke`** — revokes an anchor by its random `revocation_pointer` (never by the
  nullifier), so a published revocation never reveals which person was revoked.

## How uniqueness and unlinkability coexist

A **scope nullifier** `sha256(canonical.encode([DOMAIN, scope, holder_secret]))` is
deterministic per `(secret, scope)` — so one person yields one anchor per scope (double-
registration is detectable) — yet two scopes yield uncorrelated nullifiers. The holder secret
is a 256-bit CSPRNG value generated holder-side and never derived from PID material (kills
grinding); the inputs are `canonical.encode`-framed (kills concatenation ambiguity).
**Pairwise DIDs** derive an independent secp256k1 keypair per scope (`did:pls:<address>`), so
a person cannot be correlated across scopes by their fabric identity.

## Revocation without leaks or races

Revocations append to a signed `fabric.feed` (audit + `check_conflict` equivocation proof),
keyed by the random pointer. A **`StatusCommitment`** is the authority's signature over
`(scope, status_root, length, epoch)` of a sorted, domain-tagged Merkle **status tree** that
supports **non-membership** proofs (the primitive the untagged inclusion-only
`crypto.merkle_root` cannot provide soundly). A vote is checked against a fixed epoch
snapshot, so a stale non-membership proof cannot satisfy a later commitment — the revocation
race is eliminated. GDPR Art.17 erasure = append a revoke (reason `art17-erasure`) + crypto-
shred the off-fabric secret; the fabric held no PII and the nullifier becomes unlinkable once
the secret is destroyed (the pattern EDPB Guidelines 02/2025 endorse).

## The verifier seam (ZK upgrade with no migration)

`verifier.Admission` is the stable seam — exactly the non-`kind`/`verifier` content of an
anchor. Swapping the backend changes nothing on the fabric:

- **`TrustedRPVerifier`** (phase-1, pure-Python now): the node is a registered eIDAS Relying
  Party; it validates the presentation, enforces a **multi-issuer trust registry** (EUDI
  primary + a non-EUDI fallback issuer class, so no single-issuer monopoly), and derives the
  nullifier + pairwise DID. Honest trust statement: the RP sees the holder secret at admission
  and must handle it ephemerally; uniqueness is RP-vouched, not yet trustless.
- **`ZkVerifier`** (phase-2, dependency-gated): verifies a BBS+/SD-JWT-VC/SNARK proof that a
  hidden valid EU-PID backs the nullifier (RP never learns the PID). Fenced behind a lazy
  import so importing `personhood` never pulls a SNARK/pairing toolchain (unavailable on this
  PEP-668 box — see `DEPENDENCY_READINESS.md`). It raises a clear "dependency-gated" error.

## The gate vBank consumes

`gate.enroll(...)` admits a person into a scope once (refusing a second anchor for the same
nullifier). `gate.require_personhood(...)` gates an action — verify, require an anchor, check
the validity window, check epoch-pinned non-revocation — and returns a `PersonhoodTicket`.
The ticket authorizes *an action* but is **decoupled from the action's content signature**
(the ballot/pledge is signed by the pairwise key), which is the seam receipt-freeness / a ZK
content layer slots into later. The minimal `knitwebs/vbank` stub proves the consumption:
a ballot is impossible without a matching ticket and carries the nullifier, never identity.

## Irreversible decisions locked on day one

1. Nullifier = holder-secret + scope, `canonical.encode`-framed, **scheme-versioned**
   (scheme 0 now; EC-VRF scheme 1 reserved). *Accepted trade-off:* scheme 0 is trusted-RP and
   a future scheme-1 upgrade does not preserve one-person-one-vote across the version boundary.
2. Anchor is **co-signed** (verifier + holder pairwise key).
3. `issuer_trust_anchor` is a **registry** with an `issuer_class` incl. a **non-EUDI fallback**.
4. `revocation_pointer` is a **random commitment decoupled from the nullifier**; validity is
   **epoch-pinned** to a signed status-tree root.
5. Status tree is a **domain-tagged sorted Merkle** primitive, never `crypto.merkle_root`.
6. Every key/nullifier reference carries an explicit **scheme version** (PQ-ready).
7. `PersonhoodTicket` **decouples authorization from content signature**.
8. The anti-PII whitelist is the **floor, not the ceiling** — the residual leak surface is the
   RP's memory and the transport (timing/IP), not the record. Future hardening: separate the
   admission node from the voting node; relay/mixnet + epoch-batching.

## eIDAS 2.0 / EUDI feasibility (researched 2026-06-19)

In-spec today: PID (Reg. 2024/2977), `age_over_18` selective disclosure, per-RP scoped
pseudonyms (the nullifier pattern, ARF-blessed), OpenID4VP/VCI, eIDAS Trusted Lists, IETF
Token Status List revocation. Every Member State must offer an EUDI Wallet by **Dec 2026**.
Being a verifier needs a **WRPAC + RP-registry** onboarding (real-world, off-fabric, not a
dependency). True multi-show ZK unlinkability (BBS+/BBS#/SNARK) is ARF **roadmap**, not yet
shippable — hence the phase-1 trusted-RP backend with the ZK seam pre-wired.

## Build & test

```bash
PYTHONPATH=src python3 -m pytest tests/property/test_personhood_*.py tests/property/test_vbank_gate_stub.py -q
PYTHONPATH=src python3 -m pytest tests/property -q   # full regression
```

Modules: `src/knitweb/personhood/{records,nullifier,pairwise,status_tree,revocation,
verifier,gate,anchor,errors}.py`; consumer stub `src/knitweb/knitwebs/vbank/`.
