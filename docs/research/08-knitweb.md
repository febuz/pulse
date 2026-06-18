# Paper 8 — KnitWeb: A Woven P2P Knowledge Web

**Subtitle:** *Knitting threads where blockchains chain blocks and hashgraphs graph hashes — and how the three cooperate to run a peer-to-peer game on shared machines.*

**Status:** Concept paper v0.4
**Language:** English with Dutch summary
**Scope:** The new word *knitweb*; data model; weaving protocol; trust; the pulse/draft compute layer over donated GPU/RAM; verifiable compute on untrusted machines; how blockchain + hashgraph + knitweb cooperate for the MOLGANG P2P game; the OriginTrail heavy-artifact and provenance graph; a worked end-to-end scenario; integration with VirtualPC and the knitweb reference implementation; heritage, vocabulary crosswalk, epistemology, transport and governance.

> **Vocabulary rule.** This project is a **web**, never a "network"/"net" — a network is static nodes; a *web*, like a brain, lives through the **pulses** between its connections. The brand terms are **Web · Loom · Knit · Pulse · Fiber**, the coined data-structure word is **knitweb**, and the heavy companion graph is **OriginTrail**.

> **Normative note (reconciles this paper with the code — read before the narrative).**
> This is a *concept paper*; the code in `src/knitweb/` is authoritative where they differ.
> - **Seven primitives (normative):** `Blob` · `Fiber` · `Loom` · `Knit` · `Braid` · `Web` · `Pulse`.
>   **`Yarn` and `stitch` are narrative aliases only** — not primitives, not in code (a *yarn* ≈ an
>   account's secp256k1 keypair/identity; a *stitch* ≈ a signed, content-addressed record).
> - **`Fiber` is a *state commitment*, not a transferable coin.** A `Fiber` is an immutable snapshot of
>   one account's state (a `Braid` link). The transferable value is an integer balance of a *symbol*
>   (native symbol = **PLS**) moved by a `Knit`; "Fiber" is the brand coin name, never itself transferred.
> - **PLS is the active pay-token; the ticker FBR is reserved and *not active*.** Read every "Fiber (FBR)"
>   below as "Fiber (value unit; ticker FBR reserved — not active)".
> - **L2 today is stdlib-`asyncio`** signed-feed sync + static peers; py-libp2p / DHT are optional later
>   backends, not the current layer.

---

## Table of contents

1. [Introduction](#1-introduction)
2. [A coined word: *knitweb*](#2-a-coined-word-knitweb)
3. [Core concepts](#3-core-concepts)
4. [Data model](#4-data-model)
5. [Weaving protocol](#5-weaving-protocol)
6. [Trust and consensus](#6-trust-and-consensus)
7. [The compute layer: pulses and drafts](#7-the-compute-layer-pulses-and-drafts)
8. [Verifiable compute on untrusted machines](#8-verifiable-compute-on-untrusted-machines)
9. [Three primitives, one game: blockchain + hashgraph + knitweb](#9-three-primitives-one-game-blockchain--hashgraph--knitweb)
10. [OriginTrail: light triples, heavy trails](#10-origintrail-light-triples-heavy-trails)
11. [MOLGANG reference architecture and end-to-end walkthrough](#11-molgang-reference-architecture-and-end-to-end-walkthrough)
12. [Query model](#12-query-model)
13. [Integration with VirtualPC and the knitweb reference implementation](#13-integration-with-virtualpc-and-the-knitweb-reference-implementation)
14. [Security considerations](#14-security-considerations)
15. [Comparison with related work](#15-comparison-with-related-work)
16. [Why KnitWeb improves on legacy P2P file-sharing](#16-why-knitweb-improves-on-legacy-p2p-file-sharing)
17. [Appendix A — heritage, vocabulary, epistemology, transport, governance](#17-appendix-a--heritage-vocabulary-epistemology-transport-governance)
18. [Open questions and future work](#18-open-questions-and-future-work)
19. [Dutch summary](#19-dutch-summary)
20. [References](#references)

---

## Abstract

**KnitWeb** is a peer-to-peer protocol for weaving local knowledge updates into a shared, decentralised knowledge graph. It treats every atomic fact as a signed, content-addressed *stitch*, groups stitches into *threads* owned by *yarns* (self-sovereign identities), and lets peers (*looms*) weave threads into local *patches* that merge via Conflict-free Replicated Data Types (CRDTs). The result is a durable, eventually-consistent fabric of triples with sovereign identity, local trust, and rich querying — and no central coordinator.

This revision makes three additions. First, it positions **knitweb** as a deliberate third coinage beside *blockchain* and *hashgraph*: where a blockchain chains blocks to buy one global total order, and a hashgraph graphs hashes to buy gossiped fair order, a **knitweb knits threads** to buy coordination-free convergence. The three are not rivals; they are different consistency trade-offs, and a complete application uses each where it fits. Second, it specifies a content-addressed **compute layer** — *pulses* flowing along a *draft* — that runs over donated GPU/RAM/CPU through proof-of-useful-work workers (*spiders*), with the same reproducibility-by-content-address discipline used for verification. Where PySpark speaks of *sparks* and a *DAG*, KnitWeb speaks of *pulses* and a *draft* (a weaving draft, i.e. the drawdown). Third, it pairs KnitWeb with **OriginTrail**, the companion Decentralised Knowledge Graph that carries the *heavy* artifacts (files, images, video, audio, 3D models, books, datasets, patents) and the provenance *trails* that link them — proving "is this true and whose is it?" — while KnitWeb keeps the *light* signed triples that reference them and answers "what is the live state?".

The flagship use case is **MOLGANG**, an educational chemistry game running peer-to-peer on shared machines: blockchain settles scarce value, an in-session hashgraph fair-orders live events, KnitWeb holds the abundant mergeable world-state, OriginTrail serves the heavy assets and citation trails, and the pulse/draft engine runs the simulation on donated compute, paid in PLS (pulses).

---

## 1. Introduction

Most decentralised data systems pick one consistency guarantee and pay for it everywhere:

1. **Blockchains** force all participants onto a single linear order of blocks. This buys global total order with finality — and pays for it in throughput, latency and energy. It is the right tool when a write is *rivalrous* (a coin spent twice, a unique asset transferred twice) and a fork would be catastrophic.
2. **Hashgraphs** use gossip-about-gossip and virtual voting to agree a *fair order* of events with Byzantine fault tolerance under a known membership. Faster than a blockchain for ordering, but they still need every event linked into an ever-growing graph of hashes, and virtual voting needs a known, mostly-online membership.
3. **CRDT systems** abandon order altogether: replicas merge by a conflict-free function and converge without coordination. Cheap, available and partition-tolerant — but they deliberately cannot referee a rivalrous decision.

KnitWeb is squarely in the third camp: it is a CRDT *weave* of signed triples. But the central claim of this paper is **not** that the weave replaces the chain and the graph. It is that **knowledge is mostly non-rivalrous**, so most writes belong in the cheap, available, coordination-free layer — and the small rivalrous slice (money, unique title, fair real-time ordering) should be delegated to the narrow primitive built for it. A real application **composes all three**.

### 1.1 Why a weave?

A physical weave is strong precisely because it is made of many independent threads crossing at right angles. No single thread carries the whole load. In KnitWeb:

- **Yarns** are independent identity streams.
- **Threads** are ordered sequences of stitches from one yarn.
- **Stitches** are the atomic facts.
- **Patches** are local materialised views produced by a peer.
- **The fabric** is the union of all accepted patches.

This mirrors how human knowledge actually works: many independent sources publish, readers subscribe to sources they trust, and local consensus emerges from overlapping trust webs. Knowledge is partially ordered, multi-authored and contradiction-tolerant — so a chain or a hashgraph is the wrong shape *for the knowledge itself*. Value and finality live elsewhere (see §9); knowledge lives in the weave.

### 1.2 The cooperation thesis in one line

> **A blockchain chains blocks for one order. A hashgraph graphs hashes for fair order. A knitweb knits threads for no order — and a complete system uses each exactly where its trade-off is the cheapest one still correct.**

---

## 2. A coined word: *knitweb*

We introduce **knitweb** as a new term in the distributed-systems vocabulary, alongside *blockchain* and *hashgraph*. The morphology is load-bearing — each word's structure announces its ordering guarantee.

> **knitweb** *(n.)* /ˈnɪt.wɛb/ — the woven, peer-to-peer knowledge web that emerges when many independently signed identity-streams (*yarns*) are knitted together by CRDT semilattice merge into one durable, eventually-consistent fabric of tiny signed triples (*stitches*).
>
> Coined as the third member of the distributed-systems portmanteau family, beside **blockchain** (a *chain* of *blocks*, which buys one canonical total order) and **hashgraph** (a *graph* of *hashes*, which buys gossiped, virtual-voted fair order). Where blockchain chains blocks and hashgraph graphs hashes, **knitweb knits threads**: it deliberately trades global order for many overlapping local weaves that converge by a conflict-free merge function — sovereign, offline-first, partition-tolerant and contradiction-tolerant.
>
> *Morphology as guarantee:* a rope (**chain**) carries load in one line; a gossip-mesh (**graph**) agrees an order by voting; woven cloth (**web**) spreads load across many threads so no single thread carries the whole. Note the silent initial *k*, as in the verb *to knit*. The word names the woven artifact (the web of facts), woven by the protocol that carries the threads.

### 2.1 The core lexicon

Every coined term in this paper maps to a real, recognised distributed-systems concept. This table is the core lexicon; §7.1 extends it with the compute sub-vocabulary.

| KnitWeb term | One-line gloss | Names the concept |
|--------------|----------------|-------------------|
| **knitweb** | The woven web of signed triples | A CRDT, content-addressed knowledge graph |
| **yarn** | A cryptographically owned identity stream (one secp256k1 keypair) | Self-sovereign identity / signing principal |
| **thread** | An append-only log of stitches from one yarn | Per-identity hash-linked log |
| **stitch** | A signed, content-addressed triple + metadata | The atomic fact / signed log entry |
| **patch** | A local materialised merged view | Per-replica materialised state |
| **loom** | A peer that weaves/stores/serves patches and validates | Full node + validator |
| **spider** | A p2p web-worker that runs pulses on donated GPU/RAM and earns PLS | Compute worker / executor |
| **weaver** | An agent/process that produces or validates stitches | Application/agent producing data |
| **fabric** | The union of all accepted patches | The emergent global graph |
| **weave algebra** | The CRDT semilattice merge function | Conflict-free merge |
| **warp** *(reserved)* | The structural data axis: entities, topics, authorities | Schema axis — **not** a compute term |
| **weft** *(reserved)* | The assertion data axis: provenance-bearing claims | Schema axis — **not** a compute term |
| **pulse** | The unit of flowing compute work; you pay **PLS** ("pulses") per unit of useful work | Spark task / "spark" |
| **draft** | The lazily-built, acyclic, content-addressed plan of pulses (a weaving *draft* / drawdown) | The execution DAG |
| **pick** | One pulse run on one shard inside one shed | A scheduled task instance |
| **shard** | One partition of a bolt pinned to a loom's RAM/VRAM | Partition |
| **shed** | A maximal set of cross-free pulses that pipeline in parallel | Stage |
| **cross** | The warp re-crossing where shards repartition by key | Shuffle / exchange |
| **bolt** | A distributed, immutable, partitioned, lineage-bearing collection | RDD / DataFrame |
| **shuttle** | A compute slot on a spider carrying one pulse at a time | Executor thread / CUDA stream |
| **warper** | The driver peer for one weave (builds the draft, collects results) | Driver program |
| **reeve** | The decentralised pool scheduler / resource broker | Cluster manager |
| **Fiber** | The content-addressed account-state commitment (brand coin "Fiber"; ticker FBR reserved, not active) — value moves as a `symbol` balance (native **PLS**) via `Knit` | Native asset / ledger value |
| **PLS (pulses)** | The pay-token spent per unit of useful work | Access/utility token |
| **OriginTrail / UAL** | The companion DKG and its Universal Asset Locator for heavy artifacts | Out-of-band provenance graph + locator |

> **Naming discipline.** *Warp* and *weft* are already the two weave/data axes (entities vs assertions), so neither is reused for compute. *Thread* always means a stitch log, never a compute thread — a compute slot is a **shuttle**. *Draft* always means a **weaving draft** (the drawdown), never "a tentative first version". A short metaphor-coherence audit appears in [Appendix A.6](#a6-metaphor-coherence-audit).

---

## 3. Core concepts

| Term | Meaning |
|------|---------|
| **Yarn** | A cryptographically owned identity stream: one secp256k1 keypair (ECDSA + SHA-256). It is the same keypair as the on-chain account, so one yarn signs its stitches, holds its Fiber (FBR) balance, spends PLS, and authors its OriginTrail contributions. |
| **Thread** | An ordered, append-only log of stitches from a single yarn. |
| **Stitch** | A signed, content-addressed atomic update: a triple + metadata. |
| **Patch** | A materialised view produced by a peer after weaving selected threads. |
| **Loom** | A peer node that weaves, stores, serves and validates patches; it also hosts the donated hardware a spider runs on. |
| **Spider** | A p2p web-worker that performs useful work (GPU pulses, validation, curation) on a loom's donated hardware and earns PLS. |
| **Weaver** | An agent or process that produces/validates stitches. |
| **Fabric** | The emergent global knowledge graph = the union of all accepted patches. |
| **Weave algebra** | The CRDT merge function that combines two patches. |

---

## 4. Data model

### 4.1 Stitch

A stitch is the smallest unit of knowledge.

```json
{
  "id": "stitch://bafy...xyz",
  "yarn": "did:knit:alice",
  "seq": 42,
  "prev": "stitch://bafy...abc",
  "triple": {
    "subject": "did:knit:project/alpha",
    "predicate": "status",
    "object": "completed"
  },
  "type": "assert",
  "timestamp": "2026-06-14T12:00:00Z",
  "signature": "secp256k1-ecdsa..."
}
```

Fields:

- `id` — content-addressed identifier (CIDv1, dag-cbor, SHA-256) of the canonical, float-free serialisation.
- `yarn` — the identity (secp256k1 account) that owns the thread.
- `seq` — monotonically increasing sequence number within the yarn.
- `prev` — CID of the previous stitch in the thread (`null` for the genesis stitch).
- `triple` — subject-predicate-object statement.
- `type` — `assert` or `retract`.
- `timestamp` — wall-clock time, **advisory only** (see §6.4: it is not a referee).
- `signature` — secp256k1 ECDSA signature (over the SHA-256 of all other fields).

A triple's **object may be an OriginTrail reference** rather than an inline value, so a tiny stitch can point at a heavy artifact without ever carrying its bytes:

```json
{
  "triple": {
    "subject": "did:knit:recipe/NH3",
    "predicate": "hasBench",
    "object": "ual://<KnowledgeAsset>?ct=model/gltf&len=8421233#preview=bafyThumb..."
  },
  "type": "assert"
}
```

The reference carries just enough to decide whether to fetch (the OriginTrail UAL, content-type, byte length, optional preview/thumbnail CID). See §10 for resolution.

### 4.2 Two data axes (warp and weft)

KnitWeb's triples interlace on two axes, which is why *warp* and *weft* are reserved as schema terms:

- **Warp threads** — entities, topics, authorities (the structural, long-lived subjects).
- **Weft threads** — provenance-bearing assertions about those entities (who said what, with what evidence).

A stitch is one weft assertion crossing one or more warp entities. This is a schema convention, independent of the compute layer's vocabulary in §7.

### 4.3 Thread

A thread is an append-only chain of stitches from one yarn. Because each stitch links to its predecessor, a thread is tamper-evident *within its own yarn*: a peer verifies the whole thread by checking signatures and sequence numbers.

```
Yarn alice
  stitch[0] → stitch[1] → stitch[2] → ... → stitch[n]
```

Threads are **not** blocks. They do not bundle unrelated transactions, do not force timestamps into slots, and do not require global consensus.

### 4.4 Patch

A patch is a local materialised view. A loom chooses which threads to follow, weaves the latest stitches from each, and applies them to its local graph store.

```json
{
  "loom": "did:knit:alice-laptop",
  "asOf": "2026-06-14T12:05:00Z",
  "threads": ["did:knit:alice@42", "did:knit:bob@17", "did:knit:carol@9"],
  "root": "patch://bafy...uvw"
}
```

Patches are content-addressed. Two looms that weave the same set of thread heads produce the same patch CID, enabling efficient diff/gossip.

### 4.5 Triple store

The merged output is a labelled directed graph queryable with a subset of SPARQL or Cypher. Each triple is annotated with provenance:

- `source_yarn` — which yarn asserted/retracted it.
- `source_stitch` — CID of the stitch that introduced the change.
- `confidence` — locally computed trust score.

---

## 5. Weaving protocol

### 5.1 No blocks, no hashgraph — *inside* the weave

KnitWeb's own structure is neither a chain nor a hashgraph:

- **No blocks:** a stitch is a single fact, not a bundle.
- **No hashgraph:** stitches do not embed hashes of other peers' events. They link backwards only within their own thread.

This is a statement about the *knowledge structure*, not a prohibition on using a chain or a hashgraph elsewhere in the application. §9 shows exactly where each belongs.

Instead, the weave uses:

- **Content addressing** (CIDv1, dag-cbor, SHA-256; integers only, no floats near hashing) for stitches and patches.
- **Vector/sequence clocks** per yarn to detect causality within a thread.
- **CRDT semilattice** for patch merge.
- **Epidemic gossip** for dissemination (over a Bluetooth-LE mesh + internet bridge where deployed).

### 5.2 Gossip pattern

A loom periodically asks a subset of peers:

1. "Which yarns do you follow and what is the latest `seq` you have for each?"
2. If a peer has newer stitches, fetch them by CID.
3. Verify signatures and thread continuity.
4. Apply to the local patch using the weave algebra.

Because stitches are content-addressed, the same CID can be fetched from any peer that has it — no need to trust a specific seed node.

### 5.3 Weave algebra (merge function)

A patch is a state-based CRDT. The default merge takes the union of stitches and resolves each `(yarn, subject, predicate)` key by highest sequence number, with a deterministic tiebreak and `retract` beating `assert` on a tie:

```
merge(P1, P2) = {
  for each (subject, predicate) key:
    choose the stitch with the highest (seq, then deterministic tiebreak)
    if tie on type: retract beats assert
}
```

This is associative, commutative and idempotent, so epidemic gossip converges without coordination.

> **Not everything is last-writer-wins.** LWW is correct only for genuinely single-valued fields (a status, a pointer). Multi-valued and counting state needs the right CRDT: a player's **inventory** is an **OR-Set** (add/remove without losing concurrent adds) and a play-balance (XP) is a **PN-Counter** — two concurrent `+1`s must total `+2`, not collapse to `+1`. A PN-Counter prevents lost updates but **does not enforce conservation** (the owner can mint into their own counter freely), which is why it is only ever used for non-authoritative XP/play balances; rivalrous **coin balances settle on-chain (§9), never in a counter**.

### 5.4 Retractions and tombstones

Deletion is modelled as a `retract` stitch. A retract does not erase history; it marks a triple invalid in the latest patch, giving an automatic audit trail.

---

## 6. Trust and consensus

### 6.1 No global consensus

KnitWeb does not require all peers to agree on one true state. Each loom has its own trust policy: a bank loom might reject yarns without KYC; a research loom might prioritise open-access yarns; a friend-group loom might follow only its members.

### 6.2 Subjective trust

Each loom maintains a trust vector:

```json
{ "did:knit:alice": 0.9, "did:knit:bob": 0.7, "did:knit:carol": 0.4, "did:knit:mallory": 0.0 }
```

A triple's confidence derives from the trust of its source yarn and the transitive trust of any endorsing yarns. Contradictions are allowed to coexist, each with provenance and a credence score; readers weigh them, the web does not erase them.

### 6.3 Web of trust endorsements

A yarn can publish an endorsement stitch (`bob trusts alice`). Endorsements form a web of trust that looms use for bootstrapping and Sybil resistance without a central authority.

### 6.4 Why a CRDT is not a referee

This is the hinge for §9. The weave's conflict resolution is **attacker-grindable**: a yarn can assign any `seq` it likes to its own stitches, and the deterministic tiebreak (CID/identity order) can be ground by trying nonces, so the merge faithfully converges on whichever of two conflicting spends the attacker engineered to win. The CRDT cannot referee which spend is *real*. That is perfectly safe for lore, progress and beliefs — and **catastrophic** for money: if two conflicting spends of one coin both merge, the CRDT has just *minted* money. The weave deliberately has no referee, which is exactly why rivalrous, must-pick-exactly-one decisions are delegated to a primitive that does (a hashgraph for fair order, a blockchain for final settlement). Knowing what the weave *cannot* do is what lets the rest of the system stay cheap.

### 6.5 Notary looms (optional)

For contexts needing stronger assurance (legal contracts, supply-chain events), independent **notary looms** can co-sign stitches. This is not consensus; it is witnessed attestation. A receiving loom decides how many notary signatures it requires.

---

## 7. The compute layer: pulses and drafts

A peer-to-peer game needs more than a knowledge graph — it needs to *run code* (physics, chemistry simulation, NPC/LLM inference, rendering) on whatever GPU/RAM/CPU peers are willing to donate. The workers that do this are **spiders**: p2p web-workers that crawl the web for funded demand, run useful work, and earn **PLS** (pulses). The compute layer borrows Spark's dataflow model and re-grounds it in content addressing so results are reproducible and verifiable. The metaphor is exact: **PySpark has *sparks* and a *DAG*; KnitWeb has *pulses* and a *draft*.**

- A **pulse** is the atom of work: a pure, deterministic function `(op-CID, input-shard-CIDs) → output-shard-CID`. It is the unit of scheduling, of verification, and of payment (you pay PLS per unit of useful work).
- A **draft** is a *weaving draft* — the drawdown a weaver mounts on the loom before throwing a single pick. It is the lazily-built, acyclic, content-addressed plan of pulses and their partial order. Same draft CID + same input yarns ⇒ same result patch. The draft doubles as the lineage graph.

> *Read "draft" as the handweaving term (the threading/tie-up/treadling diagram), not "a rough first version". The whole cloth is determined by the draft before the shuttle moves.*

### 7.1 PySpark ↔ KnitWeb crosswalk

| PySpark | KnitWeb | Note |
|---------|---------|------|
| Spark engine / `SparkContext` | **Spider runtime / SpiderContext** | The compute substrate; spiders run on looms' donated hardware, so storage (loom) and compute (spider) co-locate on one machine. |
| `spark-submit` / Job | **Weave** (a job) | The full execution triggered by one cast-off; submitted as a signed job stitch carrying the draft CID + input bolt CIDs, so submission is decentralised and replayable. |
| Driver program | **Warper** | Coordinating peer: builds the draft, cuts it at crosses, schedules picks, collects the result. A *role* name, not the warp data-axis. Driver loss is recoverable — draft + inputs are content-addressed and re-submittable. |
| Executor / worker | **Spider** | A p2p worker donating GPU/RAM/CPU, advertising N pulse-slots; donated capacity is a signed capacity stitch, paid in PLS via proof-of-useful-work. |
| Executor thread / slot | **Shuttle** | An execution slot on a spider carrying one pulse at a time. *Shuttle*, never *thread*. |
| DAG of transformations | **Draft** | The headline coinage. Lazy, acyclic, content-addressed plan; compiles into sheds; doubles as lineage. |
| Stage | **Shed** | A maximal set of cross-free pulses that pipeline in parallel; boundaries fall at crosses. |
| Barrier stage / BSP superstep | **Loom-beat** | A shed whose picks advance in lockstep across spiders (a barrier-synchronised shed) — for distributed inference and tightly-coupled solves. |
| Task | **Pick** | One pulse on one shard inside one shed. Idempotent, replayable from its draft node. |
| Transformation (lazy) | **Stitch-op** | Appends a node to the draft without running it; a pure function over input shards. Narrow = *inline*, wide = *crossing*. |
| Action (eager) | **Cast-off** | Casting off secures finished knitting: the laziness-breaking op that compiles the draft and runs the pulses. |
| RDD / DataFrame | **Bolt** | An immutable, partitioned, lineage-bearing collection (a "bolt of cloth"); CID over its shard manifest; recomputable. |
| Partition | **Shard** | Unit of parallelism/placement; one shard pinned to one loom's RAM/VRAM. (Not *warp*/*skein* — those are taken.) |
| Partitioner | **Winder** | Decides which shard a datum lands in; also the cross partitioner. |
| Narrow dependency | **Inline pulse** | Output shard depends on one co-located input shard; no inter-machine hop. |
| Wide dependency | **Crossing pulse** | Output shard depends on many input shards across spiders; ends a shed. |
| Shuffle | **Cross** | The warp re-crossing: shed closes, shards repartition across spiders by key, new shed opens. The costly inter-machine boundary. |
| Shuffle files | **Skein blocks** | CID-addressed spilled intermediates; pullable from *any* holder, removing Spark's single-producer shuffle weakness. |
| Cluster manager (YARN/K8s) | **Reeve** | The decentralised resource broker: tracks donated capacity + trust, matches pulse demand to spider supply, settles PLS payment, admits/evicts spiders. |
| DAGScheduler + TaskScheduler | **Drafter + Dispatcher** | Drafter cuts the draft at crosses; Dispatcher throws picks to shuttles, preferring on-loom locality. |
| Data locality | **On-loom locality** | Prefer the spider already holding the input shard (or a pinned OriginTrail blob), avoiding a cross and host↔device copies. |
| Broadcast variable | **Bobbin** | A small read-only datum wound once and shipped to every spider by CID (the periodic table, reaction constants, an NPC system prompt). |
| Accumulator | **Tally** | A CRDT counter/G-set merged by the weave algebra — correct under retries *provided contributions are content-addressed* (idempotent merge), stronger than Spark accumulators which double-count on re-execution. |
| Lazy evaluation | **Slack** | The draft is held slack until a cast-off tensions it. |
| Lineage recompute | **Re-weave** | A lost shard is rebuilt by re-running just its sub-draft elsewhere to the *identical* CID. Provenance and fault-tolerance are one artifact. |
| Cache / persist | **Pin** | Hold a materialised shard/bolt in RAM/VRAM; content-addressed, so a pin is a *cluster-wide* cache. |
| Checkpoint | **Selvedge** | A durably-anchored finished edge; its CID can be anchored on-chain for disputes. |
| Catalyst optimiser | **Draft optimiser** | Predicate/projection pushdown into stitch-ops, shed fusion, cross minimisation — on the content-addressed draft, so semantically-equal drafts can be proven equal. |
| Structured Streaming | **Tick-weave** | The continuous game loop: each tick submits a micro-draft over the latest state delta. |
| Watermark | **Pick-clock** | The per-tick logical clock bounding how late a straggling pick may arrive before its result drops from the frame. |
| GPU/off-heap memory | **VRAM shard residency** | GPU-pulse shards pinned in donated VRAM; the dispatcher is device-aware. |

### 7.2 Reproducibility theorem (informal)

Because every pulse is a pure function over content-addressed inputs and every draft is acyclic, **the same draft CID over the same input yarns yields the same result patch** — for deterministic pulses. This single property gives, at once: cluster-wide caching (`pin`), free lineage recompute (`re-weave`), and the cross-checking that makes the verification in §8 possible. The precondition — *determinism* — is not free; §8.1 states it as a hard contract.

---

## 8. Verifiable compute on untrusted machines

Donated machines are not trusted machines. A spider can be slow, can crash, or can lie about a result to win PLS or to cheat the game. KnitWeb uses **proof-of-useful-work with sampled re-execution**: a fraction of every spider's pulses are independently re-run by peers, and any spider whose output CID does not match is **slashed**. Content addressing is what turns "did you really compute this?" into a check anyone can run.

### 8.1 The deterministic-pulse contract

CID-equality is only a valid check if honest spiders computing the same pulse produce the same bytes. Pulses **must** therefore be deterministic: no wall-clock, no unsynchronised RNG, pinned operator versions, and a fixed reduction order for floating-point. **This is genuinely hard on GPUs** — float-reduction order, atomics and library drift break bitwise determinism. Where verification matters, use integer/fixed-point kernels or a pinned, reduction-ordered float path; non-deterministic ops are barred from sampled checks or routed to a single trusted spider. Integer/fixed-point removes float-rounding nondeterminism, but a **fixed reduction tree is still required** for bitwise agreement across differing GPU architectures. We state this as a precondition rather than pretend it away.

### 8.2 Quorum-pulse (m-of-k CID agreement)

Sampled re-execution catches a lying spider after the fact; for safety/economy-critical pulses the dispatcher checks *before* accepting. It sends the pulse to *k* spiders and accepts the result only when at least *m* report the **same** output CID. A lying spider is outvoted by CID disagreement and slashed. This is straggler mitigation and Byzantine tolerance in one mechanism — but it is sound **only** under the §8.1 determinism contract and only when honest spiders are a majority of the *k* sampled, which on an open, Sybil-prone pool requires **trust-/stake-weighted sampling**, not uniform random *k*. It also costs *k×* the compute, so it is a **selective trust/cost knob**, not blanket free BFT. Cheap world-state pulses run un-replicated; the stability solve behind a tradeable asset runs `k=3, m=2`.

### 8.3 Double-shuttling and re-weave

A straggler is **double-shuttled** — the same pick is run on a faster spider and the first content-addressed result wins (safe, because the result is deterministic). A shard lost to a crash is **re-woven** from its sub-draft on another spider to the identical CID. Because skein blocks are content-addressed, a receiving spider pulls a missing shuffle block from any holder, not only its producer.

### 8.4 Anti-cheat boundary

Authoritative physics/chemistry on peer GPUs invites tampering. Competitive or economic outcomes therefore lean on quorum-pulse and, where needed, a quorum restricted to higher-trust spiders; donated compute is never assumed trustworthy by default. The selvedge CID of a contested result can be anchored on-chain (§9) as a dispute checkpoint.

---

## 9. Three primitives, one game: blockchain + hashgraph + knitweb

KnitWeb is the CRDT layer of a **three-consistency stack**. The three primitives are complementary, not competing: each is the cheapest primitive still *correct* for its slice of the game. Blockchain and hashgraph are narrow, optional services for the small rivalrous slice; the weave carries the abundant rest.

| Layer | Guarantee it buys | What it costs | Job in MOLGANG |
|-------|-------------------|---------------|----------------|
| **Blockchain** | One global total order, with finality | Throughput + seconds latency; never on the render path | Scarce ledger: Fiber (FBR) coin balances, mint/burn, unique-asset title, final auction settlement, PLS compute payments, dispute anchors |
| **Hashgraph** *(per-session)* | Fair-order BFT under >2/3-honest, mostly-online membership; a median consensus timestamp makes minority backdating/front-running infeasible (a >1/3 colluding membership can still bias order) | Needs a bounded, known validator set; ~100–150 ms | In-session live ordering: bid arrival, trade matching, matchmaking, reagent-grab races, anti-cheat timestamps, reeve lease events |
| **KnitWeb** | Coordination-free eventual consistency; max availability | No referee; peers may briefly differ | The bulk: quest/XP/research progress, recipe definitions, NPC dialogue/affinity, fungible inventory, periodic-table facts, the trust graph — **and the compute drafts themselves** |

### 9.1 Why each trade-off fits its job

- **Blockchain — because a coin has a true double-spend problem.** Two conflicting spends of one coin, or two transfers of a one-of-a-kind asset, are mutually exclusive; the system *must* pick exactly one, irreversibly, that every honest peer agrees on forever. That is the definition of total order with finality. A coordination-free CRDT cannot, in general, referee a contended rivalrous write between mutually-distrusting authors — admitting both spends mints money (single-writer or escrow CRDTs help only when ownership is uncontested). A hashgraph pays for fair timestamps on *every* event, which a low-rate settlement ledger does not need. You pay blockchain's latency precisely where write rate is low and a fork is catastrophic.
- **Hashgraph — because live play needs agreed order, briefly.** In the last seconds of an auction, eight players blitz-bid; the system must agree an order no one can backdate or front-run, in well under a second. Hashgraph's gossip-about-gossip + virtual voting gives fair ordering with a *median* consensus timestamp no minority can game. A CRDT cannot (its seq/tiebreak ordering is attacker-chosen); a blockchain is too slow for a sub-second bid flood. **Honest scope:** virtual voting needs a known, mostly-online, >2/3-honest membership — so this is a *per-session/per-lobby* shard with the reeve as membership authority, checkpointed to blockchain at session end, **not** an open global hashgraph.
- **KnitWeb — because ~95% of writes have no real conflict.** Two players discovering the same reaction, a peer advancing her own quest, an NPC's local mood: all commutative, all merge by CRDT, all converge with zero coordination. The weave's trade-off maximises availability and write throughput and supports offline play, accepting only that two peers may briefly hold different beliefs — harmless and self-healing for lore and progress. Forcing total order on commutative facts would throttle the whole game to settlement speed.

### 9.2 The hand-offs are the hard part (and they are designed, not hand-waved)

Cooperation lives in the **idempotent checkpoints** between layers:

```
   live event              fair order               final settlement            belief mirror
 (player bids)   ──▶   HASHGRAPH (per session)  ──▶   BLOCKCHAIN (one tx)   ──▶   KNITWEB (stitch)
                       median consensus ts             keyed by hashgraph          ownedBy belief,
                       fair order                      event CID (idempotent:      reputation bump,
                                                        at-most-once)               annotations
        ▲                                                     │
        │                                                     ▼
   compute payment  ◀───────────────  selvedge CID anchored for disputes
   (PLS to the spiders, same tx)
```

- A hashgraph **verdict** becomes a blockchain **settlement** keyed by the hashgraph *event CID*. The settlement contract records each consumed event-CID and rejects duplicates atomically (at-most-once), and accepts only CIDs signed by the reeve-certified session membership — binding the event to its authorised session, so replays cannot double-settle.
- A blockchain **event** updates the KnitWeb **ownership belief-mirror** (`deed:x ownedBy did:knit:winner`) — a convenience copy of the on-chain truth, never the source of truth for value.
- The spiders who ran the simulation are paid in the *same* settlement transaction (PLS), tying the §7 economy to the §9 ledger.

### 9.3 "Why not just one ledger?"

Because collapsing the stack makes every commutative fact pay settlement latency, or makes money mergeable (and thus mintable). Each plane is the cheapest correct primitive for its slice; the cost of merging them is either throughput (one chain for everything) or correctness (one CRDT for everything). For deployments that do not need value or competitive ordering, blockchain and hashgraph are simply omitted and the game runs on KnitWeb + OriginTrail alone.

---

## 10. OriginTrail: light triples, heavy trails

KnitWeb is deliberately a **light** plane: each stitch is a few hundred bytes, gossiped freely and merged by CRDT. It must never carry a 50 MB textured mesh or a research PDF. **[OriginTrail](https://github.com/origintrail)** — the established Decentralised Knowledge Graph (DKG) — is the companion **heavy** plane that does that heavy lifting and links the heavy things to each other with provenance trails. KnitWeb *complements OriginTrail, never competes with it*: OriginTrail answers **"is this true, and whose is it?"**; KnitWeb answers **"what is the live state, and who believes it?"**.

### 10.1 Division of labour

| | **KnitWeb** | **OriginTrail (DKG)** |
|--|-------------|------------------------|
| Carries | Tiny signed RDF triples | Knowledge Assets: large immutable artifacts + provenance |
| Examples | facts, beliefs, state, trust, references, compute drafts | files, images, video, audio, 3D models/textures/LODs, PDFs, books, datasets, patents; real sources (IFRS filings, news, image/video libraries) |
| Mutability | Mutable (retract/reassert) | Immutable (a new version is a new Knowledge Asset / UAL) |
| Answers | "what is asserted, and who signed it" | "here are the actual bytes, and exactly where they came from" |
| Transport | epidemic gossip (light) | DKG resolution + swarming (heavy, out-of-band) |

Both planes share one **identity** layer (a yarn signs its stitches *and* its OriginTrail contributions) and one **content-addressing** discipline. The strict rule: a stitch never inlines a blob, only a typed UAL reference; OriginTrail never stores a mutable belief, only addressable artifacts and lineage. Mutability lives **only** in KnitWeb.

### 10.2 Reference mechanism

A KnitWeb stitch points into OriginTrail by making its triple object (or a metadata field) a **UAL** (Universal Asset Locator) — OriginTrail's stable identity/locator for a Knowledge Asset — optionally narrowed to a specific artifact CID:

```
ual://<KnowledgeAsset>[?rel=<trail-edge>&ct=<content-type>&len=<bytes>][#cid=<artifactCID>|preview=<thumbCID>]
```

Resolution is strictly **two-phase and out-of-band**:

1. **Learn for free.** A loom weaving the patch sees the stitch and learns the asset's UAL — *no bytes transferred*.
2. **Fetch on demand.** Only when the object is actually needed (render the model, open the dataset) does the loom resolve the UAL against the DKG, pulling Merkle-chunked bytes from *any* holder and verifying each chunk against its SHA-256 content address.

Because both the reference and the bytes are content-addressed, the fetch needs no trust in the serving peer and the author may be offline; the binding is cryptographically pinned end-to-end — you cannot swap the model out from under the fact. The compute layer (§7) reuses the *same* CIDs, so a pulse consuming an OriginTrail dataset is itself deterministic and verifiable.

> **Versioning.** A new artifact version is a new Knowledge Asset and therefore a new UAL. "Latest" is a mutable indirection that lives **only** in KnitWeb (a stitch that retracts-and-reasserts to repoint), preserving the old reference as audit history. OriginTrail names are never mutable.

### 10.3 What a *trail* is

A **trail** is a directed, content-addressed provenance/citation path through OriginTrail's DKG — the heavy analogue of a KnitWeb thread. Where a thread is the lineage of one identity's *claims*, a trail is the lineage of the *artifacts* those claims are about.

- **Nodes:** artifacts and the entities around them — Author, Paper, Patent, Dataset, Image/Figure, Book, Model.
- **Edges (signed):** `authored-by`, `cites`, `derived-from`, `supersedes`, `depicts`, `measured-by`, `sampled-from`, `licensed-under`.

A canonical academic trail runs `author → paper → patent → dataset → figure → in-game 3D model`, every hop a signed, content-addressed edge. Trails are addressable, immutable, append-only via supersede edges, and walkable in *both* directions (what derives **from** this dataset; what this image derived **from**). A stitch can cite a whole trail to ground a belief in a real, fetchable, audited source. This is what makes OriginTrail a knowledge graph, not just a blob store — and why MOLGANG can turn a game action into a *cited* chemistry lesson.

### 10.4 Availability is not integrity

Content addressing guarantees the *correct* bytes **if** someone serves them — not that anyone does (the dead-torrent problem). OriginTrail therefore needs a pinning/replication/incentive story: storage and serving are paid in **PLS**, popular artifacts are widely pinned, and the stitch's `preview`/`len` hints let a loom degrade gracefully (show the thumbnail, mark the asset unavailable) when the full blob is gone.

### 10.5 In the brand fabric

OriginTrail is the heavy artifact-and-provenance tier; **Pulse/PLS** pays for the work; **Fiber (FBR)** is the value unit; **Loom** validates and serves the light triples; **Spiders** run the pulses; **Braid** keeps each yarn's local history; and **Web** is the woven global graph itself. (See [§13.1](#131-relationship-to-the-knitweb-reference-implementation).)

---

## 11. MOLGANG reference architecture and end-to-end walkthrough

**MOLGANG** is an educational chemistry game: a periodic-table sandbox with crafting, quests, an NPK/fertiliser track, NPCs, a research tree, an economy and P2P trading. Running it peer-to-peer on shared machines exercises every layer in this paper.

### 11.1 Latency budgets (design targets, not benchmarks)

| Path | Target | Layer |
|------|--------|-------|
| Render frame | ~16.6 ms (60 Hz) | local + pinned VRAM shards |
| Netcode tick | ~50 ms | tick-weave micro-draft |
| Live fair-order round | ~100–150 ms | per-session hashgraph |
| Value finality | seconds | blockchain |

These are targets. WAN gossip, cold OriginTrail fetches, and crosses (shuffles) on consumer GPUs can blow the frame budget; the architecture degrades to lower fidelity rather than stalling.

### 11.2 End-to-end scenario — *"The Francium Auction"*

Players Mara, Alice and Bob; their machines plus an always-on VPS are all looms in the pool, each hosting a spider.

**(0) Setup.** Each peer runs a loom and pledges GPU/RAM/CPU; the **reeve** records the leases (fair-ordered by the in-session hashgraph) and advertises the pool's shuttle count. The **tick-weave** loop runs ~60 Hz render / ~50 ms netcode; each tick the warper submits a micro-**draft** of the frame's work.

**(1) KnitWeb — offline play.** Mara, offline on a train, opens her lab. Her loom holds a local patch of light triples: quest progress, unlocked research, and `recipe:NH3` whose triples include `recipe:NH3 hasBench ual://<bench-asset>` and `recipe:NH3 citesSource ual://<trail-haber>`. She finishes a quiz quest; new stitches append to her yarn. No connectivity needed. Concurrently Bob edits the shared "reactions discovered" wiki; on reconnect both merge conflict-free (OR-Set + per-key LWW).

**(2) Pulse/draft compute over shared GPU/RAM.** Crafting NH₃ runs a reaction sim. The warper compiles a **draft**: *shed 1* parses reagents (inline pulses over the reagent bolt's shards); a **cross** repartitions by element, writing skein blocks; *shed 2* solves equilibrium on GPU as a **loom-beat** (barrier-synchronised shed); **cast-off** yields the result. A **bobbin** broadcasts the rate constants; the heavy reaction **dataset** is broadcast from OriginTrail. Mara's machine has no GPU, so the reeve matched these pulses to donated GPU spiders, preferring on-loom locality. The safety-critical stability pulse uses **quorum-pulse** (`k=3, m=2`): three spiders recompute and their output CIDs must agree, so a buggy or cheating spider is outvoted and slashed. A straggler is **double-shuttled**; a shard lost when the VPS hiccups is **re-woven** from the draft to the identical CID. Result: a stable Francium ingot — and a new OriginTrail artifact.

**(3) OriginTrail — heavy fetch + trails.** To render the ingot, Bob's loom resolves `ual://<bench-asset>`, swarm-fetches mesh + textures + crystallisation cutscene from any holder, verifies each chunk by SHA-256, and pins them in donated VRAM — none of those bytes touched the gossip layer. Mara taps **"Where does this come from?"** and the loom walks the trail: `bench-model → derived-from → figure → paper (Haber 1913) → related-patent → dataset (NH BLOOM yields) → author`. Provenance becomes research-tree gameplay.

**(4) Hashgraph — fair ordering.** Back online, Mara lists the ingot at auction; in the final 3 seconds eight players blitz-bid. The in-session hashgraph shard (membership = the known auction participants) gossips the bids and assigns fair consensus timestamps that a minority cannot backdate or front-run, producing a fair order. Winner = first-highest-before-close. Needs agreed fair ordering, not a permanent chain.

**(5) Blockchain — total-order finality.** The verdict is checkpointed to the MOLGANG chain as **one atomic settlement** keyed by the hashgraph event CID (at-most-once, session-bound — no double-settlement): debit 500 Fiber, transfer `asset:francium-ingot` title, **and** pay the step-(2) spiders in PLS — all from one settled ledger, with finality so no fork can ever show double-ownership. A selvedge CID of post-auction state is anchored as a dispute checkpoint.

**(6) Back to KnitWeb.** Knowledge-not-money outcomes flow back as stitches: `deed:francium-ingot ownedBy did:knit:winner` (a belief-mirror of the on-chain truth), the winner's reputation bump, and a new annotation linking ammonia to the NH BLOOM process, citing `ual://<trail-haber>`. Every loom merges them by CRDT.

**In sum:** bulk world-state (knitweb) → verifiable shared compute (drafts of pulses on donated GPU/RAM via spiders, BFT by quorum-pulse + sampled re-execution) → heavy media + provenance (OriginTrail) → in-session fair ordering (hashgraph) → settled finality + PLS compute payment (blockchain) → belief-mirror back to knitweb. Each plane did precisely the job its consistency trade-off fits, with explicit, idempotent hand-offs.

---

## 12. Query model

A patch is stored in an embedded graph database (e.g. Oxigraph, Kùzu, or a custom index; MeTTa/Hyperon-projectable). Queries run locally.

Example: *"Which projects does Alice trust that Bob also contributed to?"*

```sparql
SELECT ?project WHERE {
  ?project contributor did:knit:bob .
  did:knit:alice trusts ?project .
}
```

Results include provenance metadata, so the caller sees which yarns contributed each binding — and can follow any `ual://` object to the heavy source in OriginTrail.

---

## 13. Integration with VirtualPC and the knitweb reference implementation

VirtualPC agents communicate via the P2P Newsgroup 2.0 layer. KnitWeb can be the underlying transport and storage for that knowledge graph, and the pulse/draft layer can be VirtualPC's shared compute fabric.

| VirtualPC component | KnitWeb equivalent |
|---------------------|--------------------|
| Agent identity | Yarn (secp256k1 account) |
| Agent post / task update | Stitch |
| Agent feed / task stream | Thread |
| Knowledge graph | Patch / fabric |
| P2P node | Loom |
| Distributed agent job | Weave (draft of pulses) run by spiders |
| Deliberation-gate attestation | Notary-loom co-sign / quorum-pulse |
| Governance registry entry | Stitch with `governance:` predicate |
| Heavy artifact (model, dataset, doc) | OriginTrail UAL reference |

Benefits: offline-first agents (weave locally, merge later); fork-tolerant collaboration (CRDT merge of concurrent edits); auditability (every belief traces to a signed stitch); sovereignty (organisations run their own looms); and **shared compute** (agents run inference/simulation on the donated spider pool with verifiable, content-addressed results).

### 13.1 Relationship to the knitweb reference implementation

This paper is the conceptual model; the `knitweb/pulse` reference implementation is the running code. They map as follows (and the implementation's non-negotiables — secp256k1 + SHA-256, integer-only money/state, float-free canonical CBOR, no founder premine — hold throughout this paper).

**Seven core primitives** (`src/`): `Blob` (account balance state) · `Fiber` (content-addressed **account-state commitment**; "Fiber" is the brand coin, but the primitive is never itself transferred) · `Loom` (validation) · `Knit` (two-party transfer of a `symbol` balance, native **PLS**) · `Braid` (local history) · **`Web`** (the woven global graph) · **`Pulse`** (the heartbeat; useful work is paid in **PLS**, "pulses"). Workers are **spiders** (verifiable GPU compute via proof-of-useful-work with sampled re-execution).

| This paper | Reference implementation |
|------------|--------------------------|
| yarn (identity) | secp256k1 account key over a `Blob` |
| stitch (signed triple) | a signed item/assertion on the `Web` |
| thread (per-yarn log) | the yarn's `Braid` (local history) |
| patch / fabric | a materialised view of the `Web` |
| weave algebra | the CRDT merge over `Web` items |
| draft / pulse / spider | the proof-of-useful-work job DAG, its tasks, and the workers that run them |
| Fiber (value unit; ticker FBR reserved, not active) / PLS | the value unit and the active pay-token |
| OriginTrail UAL | `knitweb.synaptic.origintrail.resolve_asset` (Knowledge Asset → relations) |

**Layers:** L0 core (crypto, canonical CBOR, CID) → L1 ledger (blob/fiber/loom/knit/braid/node) → L2 p2p (stdlib-`asyncio` signed-feed sync + static peers; py-libp2p/DHT optional later) → L3 fabric (Web + items) → L4 pouw (proof-of-useful-work, sampled re-execution) → L5 looms (domain plugins: finance / operational / supply-chain / chemistry) → L6 token (PLS pay-token + Fiber value unit + user tokens + chain anchors). Domain looms — including MOLGANG chemistry — are L5 plugins, never in core.

---

## 14. Security considerations

- **Identity:** secp256k1 ECDSA keys (SHA-256); the same keypair as the on-chain account. Key rotation is a special stitch.
- **Integrity:** every stitch is signed and content-addressed (float-free canonical CBOR); every OriginTrail chunk is SHA-256-verified. Money and state are integers (PLS-wei), never floats.
- **Availability:** content-addressed gossip and DKG swarming make censorship expensive; any peer can replicate. (But see §10.4 — availability needs incentives.)
- **Confidentiality:** private yarns encrypt stitches to a set of recipient identities; public yarns are plaintext.
- **Sybil resistance:** web-of-trust endorsements; optional notary looms; trust-/stake-weighted quorum-pulse for compute.
- **Compute integrity:** proof-of-useful-work with sampled re-execution and slashing (§8), bounded by the deterministic-pulse contract (§8.1) and selective quorum-pulse (§8.2).

---

## 15. Comparison with related work

Read as **complementary rows**, not a leaderboard: most systems below are the *right* tool for the slice they occupy.

| System | Ordering guarantee | Merge | Local trust | Where it fits in this stack |
|--------|--------------------|-------|-------------|-----------------------------|
| Bitcoin / Ethereum | Global total order + finality | — | no | The **blockchain** layer (value, title) |
| Hedera Hashgraph | Fair-order BFT (known membership) | — | no | The **hashgraph** layer (live fair ordering) |
| IPFS / IPLD | none (content store) | — | no | Transport for stitches and OriginTrail blobs |
| ActivityPub | per-server | limited | limited | A federated cousin; no CRDT, no compute |
| CRDT databases (Automerge, Yjs) | none (converge) | yes | no | The merge engine class KnitWeb belongs to |
| Apache Spark / Ray | DAG scheduler | — | no (trusted cluster) | The compute model KnitWeb adapts — but trusted, single-owner |
| Bacalhau / Golem (P2P compute) | job market | — | partial | Closest compute cousins; not triple-native or CRDT-integrated |
| **OriginTrail (DKG)** | provenance graph | — | yes (provenance) | The **heavy** artifact + provenance plane KnitWeb pairs with |
| **KnitWeb (+ spiders)** | **none (CRDT converge) + content-addressed compute** | **yes** | **yes** | The knowledge + compute layer; delegates value/order to the layers above |

**Honest novelty.** Content addressing, DAG dataflow, speculation-as-BFT and provenance graphs all exist. KnitWeb's contribution is the *synthesis*: signed-triple data + subjective trust + CRDT merge + a triple-native, content-addressed proof-of-useful-work compute layer + a light/heavy split with OriginTrail, composed with (not against) blockchain and hashgraph.

---

## 16. Why KnitWeb improves on legacy P2P file-sharing

BearShare, Napster, BitTorrent, The Pirate Bay and DC++ proved decentralised systems can scale — and proved that without identity, trust or accountability they drift to grey-area use. KnitWeb keeps the good parts and removes the failure modes.

| System | What it did well | Why KnitWeb improves on it |
|--------|------------------|----------------------------|
| **Napster** | Fast discovery via a central index — one subpoena kills it. | No central index; discovery via content-addressed gossip + DHT and web-of-trust subscriptions. Nothing to seize. |
| **Gnutella / BearShare** | Fully decentralised search; query-flooding doesn't scale; free-riders. | Decentralised *and* accountable: every yarn is a signed identity; DHT routing + PLS incentives reward those who carry the load. |
| **BitTorrent** | Efficient large-file swarming; anonymous, no economy. | Borrows swarming for OriginTrail Knowledge Assets, but distributes small signed stitches for live knowledge, with account identity and provenance. |
| **The Pirate Bay** | Resilient metadata index, bound to piracy. | The fabric *is* the queryable index; lawful by design — users publish their own signed knowledge with provenance. |
| **DC++** | Community hubs; reputation trapped in one hub. | Subjective, portable trust circles; signed reputation travels with your key — no moderation-shopping. |

**Lessons applied:** identity beats anonymity; content addressing scales; legal sustainability matters (own-your-data); communities need boundaries that are *portable*, not gated by a hub operator.

---

## 17. Appendix A — heritage, vocabulary, epistemology, transport, governance

### A.1 Heritage

KnitWeb is a deliberate recombination of proven ideas:

| Tradition | Contribution | KnitWeb twist |
|-----------|-------------|---------------|
| Memex / Xanadu / hypertext | Linked, addressable, versioned documents; bidirectional links | Stitches are smaller than documents; OriginTrail revives transclusion for heavy artifacts |
| Semantic Web (RDF, Linked Data, PROV-O) | Triples as the universal atom; URIs for concepts; provenance vocabulary | No global ontology required; each yarn defines its own predicates and patches merge anyway |
| Git | Content-addressed objects; Merkle lineage; offline-first; fork/merge | A yarn is a branch that signs its own commits; patches are CRDTs, not rebases |
| IPFS / IPLD / BitTorrent | Content addressing, swarming, censorship resistance | Stitches are tiny gossiped objects; heavy blobs swarm via OriginTrail |
| OriginTrail (DKG) | Decentralised provenance; Knowledge Assets; "who originated what" | KnitWeb adds the light, mutable, CRDT belief/state layer that *references* those assets |
| CRDT literature | Convergent replicated data types; semilattice merge | The weave algebra applies CRDT semantics to signed, triple-oriented facts |
| Spark / Dryad dataflow | Lazy DAG of transformations; lineage recompute | Pulses/drafts are content-addressed and deterministic, enabling cross-spider verification |
| Verifiable / self-sovereign identity | Cryptographic accountability | Every yarn is a secp256k1 account; trust is subjective and local |
| Nakamoto / Hashgraph consensus | Total order; fair BFT order | Used *alongside* the weave for the rivalrous slice, not as the substrate for knowledge |

### A.2 Vocabulary crosswalk

| KnitWeb term | Blockchain | RDF / Semantic Web | Git | Enterprise data |
|--------------|-----------|--------------------|-----|-----------------|
| Yarn | Account / wallet | Named graph (per agent) | Branch + signing key | Data owner / steward |
| Thread | Transaction list | Sequence of triples | Signed commits | Audit trail for one subject |
| Stitch | Transaction | RDF triple + provenance | Signed commit | Signed atomic fact |
| Patch | World-state snapshot | Materialised graph | Working tree + HEAD | Materialised governed dataset |
| Loom | Full node | Triple store + reasoner | Git daemon/client | Governed data-product node |
| Weave / merge | Consensus | Graph merge | Merge | CRDT reconciliation |
| Pulse / draft | — | — | — | Spark task / DAG (content-addressed) |
| trail (OriginTrail) | — | PROV-O lineage / DOI graph | — | Data lineage |
| CID | Tx hash | URI / IRI | Object hash | Content identifier |
| Trust vector | Staking | Provenance trust score | GPG web of trust | Data-quality scorecard |
| Notary loom | Validator | Trusted signature | Tag signer | Steward approval |

### A.3 Transport-vs-fabric epistemology

A common source of confusion is the relationship between the *protocol* and the *graph*:

- **KnitWeb-transport** is the moving part: looms, spiders, gossip messages, swarms and crosses that carry threads between peers. It is about **communication**.
- **KnitWeb-fabric** is the standing part: the materialised graph a loom produces by weaving selected threads into a patch. It is about **knowledge**.

A transport can run without producing one shared fabric (two isolated looms gossiping nothing to each other). A fabric can outlive its transport (a patch reconstructed from archived stitches after the originating looms go offline — knowledge outlives the wires). This separation is why the system is legally and socially sustainable: the transport does not host content; it carries signed statements. The fabric is what a loom *chooses to believe*.

| Question | Transport | Fabric |
|----------|-----------|--------|
| What is it? | P2P protocol | Materialised knowledge graph |
| Unit | Stitch (signed triple) | Patch (merged triple set) |
| One canonical instance? | No — many looms | No — each loom has its own |
| Guarantees delivery? | Gossip, retries, store-and-forward | Nothing — patches are local beliefs |
| Guarantees truth? | Nothing — only signatures | Subjective trust policies |
| Analogy | The postal system | The library assembled from received letters |

### A.4 Transport layer

KnitWeb separates the *data model* from the *transport*, so deployments choose transports without changing the weave algebra. The reference profile:

1. **WebSocket** for active loom-to-loom sessions.
2. **HTTP(S) / CID fetch** for on-demand stitch retrieval (any HTTP cache can serve a stitch).
3. **QUIC / libp2p** where NAT traversal and mobility matter.
4. **Bluetooth-LE mesh + internet bridge** (e.g. a Nostr bridge) for offline-first, room-scale or planet-scale reach.
5. **Store-and-forward** via resilient relay/mailbox looms for intermittent nodes.

Gossip message types: `HELLO`, `WANT_YARNS` (`(yarn, latest_seq)` pairs), `HAVE_STITCH` (`(yarn, seq, cid)`), `FETCH_STITCH` (CID), `PATCH_ROOT` (`(loom, patch_cid, yarn_heads)`). All messages are small. **Large objects (attachments, models, datasets, video) are never gossiped — they are referenced by an OriginTrail UAL and fetched out-of-band from the DKG swarm** (§10), keeping gossip traffic light.

> *Asset note:* the only remaining `knitnet`-named tokens are on-disk assets pending rename — the interactive page `public/knitnet.html` and `logos/knitnet-logo.svg` (→ `knitweb.*`) — and the historical corpus file `data/external/knitnet-landing-corpus.json`. These are file names, not prose. The protocol code lives under `src/integrations/lightrag/`.

### A.5 Governance mapping — from Collibra to stitches

Enterprise governance tools (Collibra, Alation, Microsoft Purview) maintain a business glossary, data dictionary, policies, stewardship, lineage and quality rules. KnitWeb represents all of these as signed triples; OriginTrail carries the heavy referenced assets and their lineage trails.

| Collibra concept | KnitWeb representation | Example |
|------------------|------------------------|---------|
| Business term | Stitch in a glossary yarn | `did:knit:glossary/customer-churn rdf:type glossary:Term` |
| Data asset | OriginTrail UAL + metadata triples | `did:knit:dataset/sales-2026 fabric:bytes ual://<asset>` |
| Policy | Assertion by a governance yarn | `did:knit:policy/pii-masking fabric:appliesTo did:knit:dataset/customers` |
| Steward | Identity in a stewardship yarn | `did:knit:dataset/sales-2026 fabric:steward did:knit:alice` |
| Lineage | OriginTrail trail of `derived-from` edges | `view/monthly-revenue derived-from dataset/sales-2026` |
| Quality rule | Stitch with `fabric:qualityScore` | `did:knit:dataset/sales-2026 fabric:qualityScore 0.94` |
| Issue / workflow | Retraction + corrective assertion | `did:knit:issue/42 fabric:status resolved` |

Because each governance statement is a signed stitch, governance itself becomes auditable, forkable and mergeable. A regulator can run a loom that follows only governance yarns and produce a patch that proves compliance without API access to a central catalog.

### A.6 Metaphor coherence audit

One weaving term, one meaning:

- **thread** = stitch log (never a compute thread → use **shuttle**).
- **warp / weft** = the entity/assertion data axes (never the compute DAG → use **draft**; never a partition → use **shard**).
- **draft** = the weaving drawdown / execution plan (never "tentative version").
- **weave** = both the CRDT merge (data) and a compute job; disambiguate by context (*weave algebra* vs *a weave*).
- **loom** = node and validator; **spider** = the compute worker that runs on it. Storage and compute co-locate, but the words stay distinct.

### A.7 Brand fabric

KnitWeb sits in one woven brand fabric over a single content-addressed, CRDT, triple-native store. The brand terms are **Web · Loom · Knit · Pulse · Fiber** — never "network"/"net".

| Brand / primitive | Role |
|-------------------|------|
| **Web** | The woven global graph (the knitweb itself) |
| **Loom** | Validation; a peer node that weaves/serves/validates |
| **Knit** | A two-party transfer on the ledger |
| **Pulse** | The heartbeat and the unit of useful work; paid in **PLS** ("pulses") |
| **Fiber** | The content-addressed account-state commitment (brand coin "Fiber"; ticker FBR reserved + not active, no premine) |
| **Spider** | The p2p web-worker selling verifiable GPU compute |
| **Braid** | A yarn's local history |
| **OriginTrail** | The external heavy artifact + provenance DKG |

Identity is the secp256k1 account key — the same key as a **yarn** — so one identity signs stitches, holds Fiber, spends PLS, runs (or hires) spiders, and authors OriginTrail Knowledge Assets. There is no founder premine; FBR is earned, and PLS tracks real usage, not hype.

---

## 18. Open questions and future work

1. **Quorum economics:** how to price *k*-fold redundant compute so safety-critical pulses are affordable but not abused.
2. **OriginTrail pinning incentives:** how to guarantee heavy artifacts stay served (beyond best-effort PLS-paid pinning) and avoid dead-trail loss.
3. **Hashgraph membership under churn:** formalising per-session validator sets and the reeve's membership authority on an open, churning pool.
4. **GPU determinism:** practical recipes for bitwise-reproducible kernels (fixed reduction trees) where verification matters.
5. **Partial replication:** how a loom subscribes to a subset of a large yarn or bolt.
6. **Garbage collection:** when old stitches/skein blocks can be archived past a selvedge without losing lineage.
7. **Query federation:** answering queries across many remote patches.
8. **Source-material licensing:** the attribution/licensing obligations for the real papers, patents and datasets that OriginTrail trails point at.
9. **Formal verification:** proving the weave algebra (and the reproducibility theorem) in a proof assistant.

---

## 19. Dutch summary

**KnitWeb** is een peer-to-peer protocol dat lokale kennisupdates weeft tot een gedeelde, gedecentraliseerde kennisgraaf. Elke identiteit (*yarn*) publiceert een ondertekende, inhoud-geadresseerde reeks feiten (*stitches*); peers (*looms*) weven die tot lokale *patches* die conflictvrij worden samengevoegd via CRDTs. Er is geen centrale coördinator en geen globale ordening.

**Een nieuw woord.** *Knitweb* wordt bewust geïntroduceerd náást *blockchain* en *hashgraph*: een blockchain rijgt blokken aan elkaar (één totale ordening), een hashgraph maakt een graaf van hashes (eerlijke ordening), en een knitweb *breit draden* (géén ordening, maar conflictvrije convergentie). De drie zijn geen concurrenten maar verschillende consistentie-afwegingen — een echt systeem gebruikt elk waar het past.

**Rekenlaag.** Waar PySpark spreekt van *sparks* en een *DAG*, spreekt KnitWeb van **pulses** en een **draft** (een weef-*draft*, de drawdown): de luie, acyclische, inhoud-geadresseerde uitvoeringsplanning van pulses, gedraaid door **spiders** op gedeelde GPU/RAM/CPU en betaald in **PLS** ("pulses"). Doordat pulses pure, deterministische functies over inhoud-geadresseerde invoer zijn, zijn resultaten reproduceerbaar en — via proof-of-useful-work met steekproefsgewijze her-uitvoering en *quorum-pulse* (m-van-k overeenstemming van output-CID's) — verifieerbaar op niet-vertrouwde machines.

**OriginTrail.** KnitWeb draagt de *lichte* ondertekende triples; **OriginTrail** (de gedecentraliseerde kennisgraaf, DKG) doet het zware werk: de grote artefacten (bestanden, afbeeldingen, video, audio, 3D-modellen, boeken, datasets, patenten) en de *trails* (herkomst-/citatieketens, met o.a. auteurs als knopen) die ze verbinden. Een stitch verwijst met een **UAL** (`ual://<KnowledgeAsset>`) en haalt zware bytes buiten de gossip om op, SHA-256-geverifieerd. OriginTrail beantwoordt "is dit waar en van wie?"; KnitWeb beantwoordt "wat is de live toestand?".

**De game.** In **MOLGANG** (een educatieve scheikundegame op gedeelde machines) werken alle lagen samen: blockchain vereffent schaarse waarde (Fiber-munten, unieke titels, PLS-rekenbetaling), een sessie-hashgraph ordent live-events eerlijk (biedingen, matchmaking), KnitWeb houdt de overvloedige, samenvoegbare wereldstaat (quests, recepten, NPC's, inventaris, kennis én de compute-drafts), en OriginTrail levert de zware assets en de citatie-trails — waardoor een spelactie een geverifieerde scheikundeles wordt. In de merkenfamilie: **Web · Loom · Knit · Pulse · Fiber** — met **spiders** als werkers en **OriginTrail** als zware kennisgraaf.

---

## References

1. Shapiro, M., Preguiça, N., Baquero, C., & Zawirski, M. (2011). *Conflict-free Replicated Data Types.*
2. Lamport, L. (1978). *Time, Clocks, and the Ordering of Events in a Distributed System.*
3. Benet, J. (2014). *IPFS — Content Addressed, Versioned, P2P File System.* (and IPLD.)
4. Zaharia, M., et al. (2012). *Resilient Distributed Datasets: A Fault-Tolerant Abstraction for In-Memory Cluster Computing.*
5. Isard, M., et al. (2007). *Dryad: Distributed Data-Parallel Programs from Sequential Building Blocks.*
6. Nakamoto, S. (2008). *Bitcoin: A Peer-to-Peer Electronic Cash System.*
7. Baird, L. (2016). *The Swirlds Hashgraph Consensus Algorithm: Fair, Fast, Byzantine Fault Tolerance.*
8. W3C. (2013). *PROV-O: The PROV Ontology.*
9. OriginTrail. *Decentralized Knowledge Graph (DKG) — whitepaper and documentation.* https://origintrail.io · https://github.com/origintrail
10. VirtualPC / knitweb project. `docs/p2p-newsgroup-2.0.md`, `src/integrations/lightrag/`, the `febuz/knitweb` reference implementation, and the landing corpus `data/external/knitnet-landing-corpus.json`.
