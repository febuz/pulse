# Knitweb — research & concept papers

Conceptual papers and design notes behind the Knitweb reference implementation. These
documents describe the *vision and protocol model*; the running code under `src/` is the
source of truth for byte-level behaviour (secp256k1 + SHA-256, integer-only money/state,
float-free canonical CBOR, no founder premine).

| Paper | What it covers |
|-------|----------------|
| [08-knitweb.md](08-knitweb.md) | **KnitWeb: A Woven P2P Knowledge Web.** Coins the word *knitweb* beside *blockchain* and *hashgraph*; specifies the **pulses + draft** compute layer over donated GPU/RAM (spiders, proof-of-useful-work, sampled re-execution); shows how **blockchain + hashgraph + knitweb cooperate** for the MOLGANG P2P game; and details the **OriginTrail interlock** — light signed triples ↔ heavy artifacts (files/images/video/books/patents) + provenance trails. Includes a §13.1 map from the paper's vocabulary to the seven core primitives (Blob · Fiber · Knitweb · Knit · Braid · Web · Pulse) and the L0–L6 layers. |
