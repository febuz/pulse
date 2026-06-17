# Fiber & the Synaptic Web

**Brand coin:** Fiber · **Pay-token:** PLS (pulses) · **Brand vocabulary:** Web · Loom · Knit · Pulse · Fiber (the seven code primitives: Blob · Fiber · Loom · Knit · Braid · Web · Pulse)

> Note: *Fiber* is the brand coin, but the `Fiber` **primitive** is an immutable,
> content-addressed account-state commitment (a `Braid` link) — never itself
> transferred. Value moves as an integer balance of a *symbol* (native `"PLS"`) via `Knit`.

> Token note: you pay in **PLS** ("pulses") for activity — not for fibers or knits.
> FBR is reserved (a possible separate regional token later).

> Vocabulary rule: this project is a **web**, never a "network"/"net". A network
> is static nodes; a *web* — like a brain — lives through the **pulses** between
> its connections. Only **Web, Loom, Knit, Pulse, Fiber** are brand terms.

## The thesis

Big models drown in a *context tax*: feeding raw data into large LLMs is energy-
and token-heavy, and impossible on edge hardware. Fiber inverts this. The Knitweb
weaves verified relations into a **Synaptic Web**, and the **Fiber Synaptic
Compiler** extracts those relations as ultralight, deterministic **bytecode** that
the smallest devices — IoT AI, AR glasses — execute locally, streamed over BLE /
5G / Wi-Fi / satellite. The device runs *inference over bytecode*, not a multi-
gigabyte context load.

```
[OriginTrail DKG]  verified origin + originators (IFRS, news, YouTube/Youku/RuTube, images)
       │
       ▼
[Knit · Loom · Web]  weave + scale the cross-source relation streams
       │
       ▼
[Fiber Synaptic Compiler]  relations → signed, content-addressed bytecode
       │
       ▼
[Edge AI / AR  (BLE/5G/sat)]  execute locally, low power, zero context tax
```

## Symbiosis with OriginTrail (complement, never compete)

- **OriginTrail = trust/provenance layer.** Its Decentralised Knowledge Graph
  proves *who originated what* and links the real sources (IFRS filings, news,
  image and video libraries across Western/Chinese/Russian platforms).
- **Fiber = execution/performance layer.** It consumes those *verified* assets and
  compiles their relation matrix to edge-executable bytecode.

OriginTrail answers "is this true and whose is it?"; Fiber answers "how do I run
it on a pair of glasses?". `knitweb.synaptic.origintrail.resolve_asset` reads a
Knowledge Asset (explicit triples *or* linked sources) into relations; it never
invents data.

## The bytecode (`knitweb.synaptic.bytecode`)

A tiny, self-describing binary format — **data, not code**, but it *is* the
"relation matrix" an edge model consumes:

```
magic "PLS1" | version | asset_cid | originator
dictionary (lexicographically sorted, interned terms, LEB128 varints)
relations[]  (subject_idx, predicate_idx, object_idx, source_type_byte, weight)
```

Properties that make it sound (Szabo principle: the artifact carries its own
guarantees):

- **Deterministic** — sorted dictionary + canonical relation order ⇒ identical
  bytes for identical relation sets ⇒ content-addressable (`bundle_digest`).
- **Reversible** — `decode_bundle` reconstructs the exact relations.
- **Provenance-bearing** — embeds the source asset CID + verified originator, and
  `sign_bundle` / `verify_bundle` let an edge device verify origin *before*
  executing. A tampered bundle fails verification and is refused.
- **Compact** — string interning + varints. The win scales with graph size and
  URI repetition (a 4-source toy is ~24% smaller than its JSON; large graphs with
  shared URIs compress far more). We report real ratios, never inflated ones.

## Where PLS (the pay-token) fits — the economic loop

This keeps the token an **access right**, not a speculation (no premine; demand-
gated mint). You pay in **pulses (PLS)** for activity — not for fibers or knits:

1. A device (AR glasses / IoT) requests a verified relation bundle for what it is
   looking at.
2. A **spider** resolves the OriginTrail asset, compiles + signs the bytecode, and
   serves it — *useful work*, verified by sampled re-execution.
3. The requester pays **PLS** for the access (one pulse per served bundle); the
   spider earns bounded PLS for the work. Value tracks usage (Principle 23), not hype.

## Szabo framing — cryptographic-legal computational rigor

- Each bundle is a **bearer instrument of verified relations**: content-addressed
  identity + originator signature = non-repudiable provenance an edge device can
  check offline.
- Settlement stays on the tiny deterministic Loom surface (integers only); the
  heavy compile/serve work lives off the settlement path and only ever commits a
  hash + a verification verdict.
- "Smart contracts" here are canonical, signed records the Loom validates — not a
  general VM — keeping the trusted surface auditable (Principle 82: code bugs
  destroy more value than 51% attacks).

## Status

`knitweb.synaptic.bytecode` and `.origintrail` are implemented and property-tested
(determinism, round-trip, provenance signing, OriginTrail resolution). Next:
register synaptic compile/serve as a proof-of-useful-work job class (Phase 4) and
wire PLS access payment to bundle delivery.
