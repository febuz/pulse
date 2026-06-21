# Pulse → Lens: the stable read-only export boundary

This document defines the **public, pure-Python surface** that a Lens (an
interpretation layer that ranks, filters, and presents the Web) may depend on.
Everything named here is stable: signatures and byte-level behaviour will not
change without a deprecation notice. Anything *not* named here — and in
particular any name beginning with `_` or any attribute like `Web._out` — is a
Pulse internal and **Lens must not depend on it**.

The boundary is deliberately small and composed only of functions that are
deterministic and side-effect free, so a Lens built against it produces
byte-identical results on every peer.

## The one call you usually want: `web_snapshot`

```python
from knitweb.fabric.snapshot import web_snapshot

snap = web_snapshot(web)          # deterministic, deep-copied, read-only
```

`web_snapshot(web) -> dict` is the single import a Lens needs for a complete,
mutation-isolated read of the Web. The returned dict is a deep copy, so a Lens
can freely mutate it without touching live Pulse state. It contains:

| key          | type            | meaning |
|--------------|-----------------|---------|
| `state_root` | `str` (64 hex)  | SHA-256 root committing to **all** nodes *and* edges |
| `node_count` | `int`           | number of nodes |
| `edge_count` | `int`           | number of edges |
| `records`    | `dict[str,dict]`| node CID → record, keys sorted by CID |
| `jsonld`     | `dict`          | deterministic JSON-LD `@graph` (DKG / OriginTrail-compatible) |

`web_snapshot` is byte-stable: two calls on the same content encode identically
under `canonical.encode`, and identical content on any peer yields the same
`state_root`.

## The underlying public modules

A Lens that needs finer-grained access than the snapshot may use these directly.

### `knitweb.core.canonical` — content addressing
- `encode(value) -> bytes` — canonical, float-free CBOR (raises on floats).
- `decode(buf) -> value` — strict inverse of `encode`.
- `cid(value) -> str` — CIDv1 (`dag-cbor` + sha2-256), the content id of a record.
- `DAG_CBOR_CODEC` — the codec constant.

### `knitweb.fabric.web.Web` — the graph (read methods only for Lens)
- `Web.get(node_cid) -> dict | None` — the record at a CID.
- `Web.neighbors(node_cid, rel=None) -> list[str]` — outgoing neighbour CIDs.
- `Web.traverse(...) -> set[str]` — deterministic membership set (`set[str]`); it
  exposes *which* CIDs are reachable, not an order (the internal walk only sorts for
  deterministic membership). For ordered lineage use `provenance.ancestry`, which
  returns an ordered list.
- `Web.size -> tuple[int, int]` — `(node_count, edge_count)` **property** (no call parens).
- `Web.nodes -> dict[str, dict]` — the **live** CID → record mapping (the Web's own
  backing dict, not a copy); treat it as read-only — Pulse does not defensively copy it,
  so mutating it corrupts live state. For a truly mutation-isolated (deep-copied) read
  surface, use `web_snapshot()`.

`Web.weave` and `Web.link` are *write* methods — a Lens reads; it does not weave.

### `knitweb.fabric.jsonld` — interchange
- `export_web(web) -> dict` — deterministic JSON-LD `@graph` (nodes sorted by CID,
  edges by `(rel, dst, weight)`); byte-identical for identical content.
- `import_web(doc) -> Web` — rebuild a Web from such a document.
- `ual_for_node(node_cid) -> str` — the UAL (DKG identifier) for a node.
- `edges_of(web) -> list[Edge]` / `validate_edge_metadata(metadata) -> dict`.

### `knitweb.fabric.provenance` — lineage (read-only traversal)
- `ancestry(web, start, rels=None) -> list[str]`
- `provenance(web, start, rels=None) -> dict`
- `origins(web, start, rels=None) -> list[str]`
- `is_acyclic(web, start, rels=None) -> bool`

### `knitweb.fabric.attest` — verify authorship & record integrity
- `verify_record(record, author_pub, sig, author_field="author") -> bool` —
  returns `False` (never raises) on malformed input, so it is safe in audit/boolean paths.
- `check_record(record, expected_cid, author_pub, sig, *, author_field="author") -> RecordCheck` —
  the same checks plus CID binding, returning a `RecordCheck(ok, reason)` whose
  `reason` names the first failure (`record-not-a-dict`, `non-canonical-record`,
  `cid-mismatch`, `bad-author-pub`, `author-mismatch`, `bad-signature`, or `ok`).
- `RecordCheck(ok: bool, reason: str)` — the verdict value.
- `Attestation`, `attest(...)`, `node_is_attested(...)` — the signing envelope and helpers.

### `knitweb.fabric.items` — domain record shapes
Frozen, integer-only, CID-deterministic record types a Lens can recognise and rank:
- `KnowledgeItem`, `ResourceItem`, `AttentionRecord`, `FabricCheckpoint`.
- `web_state_root(web) -> str` and `checkpoint(web, beat) -> FabricCheckpoint`.

`AttentionRecord` is the Lens ranking signal: optional, non-negative **integer**
metrics (`confidence`, `usefulness`, `deploy_debug`, `source_priority`,
`relation_weight`) bound to a target node CID. An absent metric is distinct from a
zero metric, so the CID reflects exactly the signals asserted.

## Minimal Lens consumption example

```python
from knitweb.fabric.snapshot import web_snapshot
from knitweb.fabric.attest import check_record
from knitweb.fabric import provenance

def rank(web, attestations):
    snap = web_snapshot(web)                      # one read, fully isolated

    ranked = []
    for cid, record in snap["records"].items():   # already CID-sorted
        att = attestations.get(cid)
        # only surface records whose authorship + content id verify
        if att and check_record(record, cid, att.author_pub, att.sig).ok:
            depth = len(provenance.ancestry(web, cid))   # lineage as a signal
            ranked.append((cid, record, depth))

    ranked.sort(key=lambda r: (-r[2], r[0]))      # deterministic order
    return {
        "state_root": snap["state_root"],         # provable view commitment
        "graph": snap["jsonld"],                  # hand to a DKG / OriginTrail sink
        "ranked": ranked,
    }
```

## The rule

A Lens depends on the names above and **nothing else** in Pulse. It must not
import private modules, read `_`-prefixed attributes, or reach into Web internals
(`Web._out`, adjacency lists, etc.). If a Lens needs something this boundary does
not expose, that is a gap to file against the `epic:interpret` track — not a
reason to reach inside. Holding this line keeps Pulse free to evolve its internals
while every Lens keeps working.
