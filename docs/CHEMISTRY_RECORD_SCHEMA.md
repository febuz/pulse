# Chemistry record schema — `reaction-knowledge` **v1** (CID-stability sign-off)

This document is the **frozen** canonical-record reference for the chemistry domain
knitweb (`src/knitweb/knitwebs/chemistry/`). It exists to satisfy the hard coordination
gate of knitweb/pulse#210: the chemistry record's byte-stable CIDs are **locked before**
the molgang plugin and its seed graph build against them, so an independent emitter (e.g.
molgang's PHP/Python reaction signer) and Pulse produce **byte-identical** records — and
therefore identical CIDs — for the same logical reaction. If two implementations diverged
on a single byte, the content-addressed Web would fork.

The schema is **version `v1`**. "Version" here is the schema contract, not a field inside
the record — see [Versioning & migration](#versioning--migration). v1 is pinned by a
frozen known-answer vector (below); any drift fails loudly in CI.

## The record — `kind: "reaction-knowledge"`

A signed reaction record is a flat map with exactly these fields
(`ChemistryKnitweb.to_record`, `src/knitweb/knitwebs/chemistry/__init__.py:180`):

| Field        | Type                  | Notes                                                              |
|--------------|-----------------------|--------------------------------------------------------------------|
| `kind`       | `str`                 | Constant `"reaction-knowledge"` (`ChemistryKnitweb.KIND`).         |
| `equation`   | `str`                 | Canonical, **term-sorted** human equation, e.g. `"2 H2 + O2 -> 2 H2O"`. |
| `reactants`  | `list[term]`          | Term records, **canonically sorted** (see below).                  |
| `products`   | `list[term]`          | Term records, **canonically sorted**.                              |
| `author`     | `str`                 | The signer's `pls1…` address (the `author_field` of the attestation). |
| `balanced`   | `bool`                | Always `true` — `emit()` refuses to sign a mass- or charge-imbalanced reaction. |
| `kinetics`   | `list[[str, int]]`    | **Optional.** Sorted `[name, integer]` pairs. **Omitted entirely when absent** (see [Versioning](#versioning--migration)). |

### Term sub-record

Each entry of `reactants` / `products` is a flat map with exactly these fields
(`to_record.term_rec`):

| Field         | Type                | Notes                                                        |
|---------------|---------------------|--------------------------------------------------------------|
| `species`     | `str`               | The species formula, e.g. `"H2O"`.                           |
| `coeff`       | `int`               | Stoichiometric coefficient (integer-only; floats/bools rejected). |
| `composition` | `list[[str, int]]`  | `[element, count]` pairs, **canonically sorted** by element; counts are integers. |
| `charge`      | `int`               | Total charge of the species (integer-only).                  |

## Canonicalization rules (what makes the CID stable)

These are the normalizations that let independent emitters converge on one CID. They are
all applied at record-build / construction time, never left to the caller:

1. **Integer-only.** Every numeric field (`coeff`, `count`, `charge`, kinetics values) is a
   strict `int`. Floats and bools are rejected at construction (`TypeError`). No floats ever
   reach the signed path.
2. **Composition is sorted.** A `Species` normalizes its `composition` to sorted
   `(element, count)` pairs at construction — `Species.make({"O":1,"H":2})` and the raw
   `Species("H2O", (("O",1),("H",2)))` both become `(("H",2),("O",1))`. Duplicate elements
   are rejected.
3. **Terms are sorted.** `reactants` and `products` are each emitted in canonical term order,
   so writing a reaction with its reactants swapped yields the **same** record and CID.
4. **Equation is term-sorted.** The `equation` string is rendered from the sorted terms, so it
   matches the structured order (`"2 H2 + O2 -> 2 H2O"`, never `"O2 + 2 H2 -> …"`).
5. **Canonical CBOR + CIDv1.** The record is encoded with the strict, float-free canonical
   CBOR codec (`core/canonical.py`) and content-addressed as dag-cbor / sha2-256 /
   multibase-base32 CIDv1. **Map keys are ordered by byte-length then lexicographically**
   (so the top-level order on the wire is `kind`, `author`, `balanced`, `equation`,
   `products`, `reactants`; within a term, `coeff`, `charge`, `species`, `composition`) —
   this ordering is the codec's, not the source dict's, and is part of what every emitter must
   reproduce.

## The frozen sign-off vector

The known-answer vector that pins all of the above at once lives in
`tests/knitwebs/test_chemistry.py` as `test_cross_impl_cid_byte_vector_is_stable`. For the
reaction `2 H2 + O2 -> 2 H2O` signed by the test author `pls1acgjmtdc45sccnrjuokpyucp6edu5yidvm`
(test key `0x11…11`, **never a real key**):

- the record is exactly `_VECTOR_RECORD`,
- it canonical-encodes to the exact bytes `_VECTOR_ENCODE_HEX`,
- and content-addresses to the exact CIDv1
  **`bafyreifkpbmrhaypnp7dr6qeytxzdbjp3zork6fi346b33ceysq5e7totu`**,
- which re-decodes byte-identically (round-trip).

**This vector is the drift gate.** Any change to the field set, ordering, encoding, or CID
logic breaks that test — schema drift fails loudly in CI rather than silently forking the Web.
A second-implementation conformance check is simply: emit this reaction with that author key
and assert the same CID.

## Versioning & migration

The schema is **v1**. It is identified by `kind: "reaction-knowledge"`, not by a version field
inside the record (adding one would itself change every CID). The rules for evolving it without
breaking v1 content addresses:

- **Additive, optional fields use conditional omission.** A new optional field must be
  **omitted from the record when it has no value**, exactly as `kinetics` is today (and as the
  Beat per-epoch mint cap does in `core/pulse.py`). A record that does not use the new field
  then keeps its v1 bytes and v1 CID, so existing content addresses never move. The frozen
  vector above (which carries no `kinetics`) must keep passing across any such addition.
- **Any breaking change bumps `kind`.** A change that would alter an existing record's bytes —
  renaming/removing a field, changing a type, reordering semantics — is **not** allowed under
  `reaction-knowledge`. It must be introduced under a new `kind` (e.g.
  `reaction-knowledge-v2`) with its own frozen vector, so v1 and v2 records coexist and v1 CIDs
  remain valid forever.
- **Cross-implementation parity is part of the contract.** Any other emitter of this schema must
  reproduce the frozen vector before it is considered conformant; a divergent CID is a defect in
  that emitter, not a new version.

## Status against #210

| #210 acceptance item                              | Status                                                            |
|---------------------------------------------------|------------------------------------------------------------------|
| Freeze the canonical field set + ordering         | ✅ `to_record` + this doc; ordering pinned by the frozen vector.  |
| Golden-CID vectors for representative records      | ✅ `test_cross_impl_cid_byte_vector_is_stable`.                   |
| Round-trip + cross-version byte-identity tests     | ✅ round-trip in the vector; cross-version = the `kind`-bump + conditional-omission policy above, with `kinetics` as the worked example. |
| Documented schema version + migration note         | ✅ this document.                                                 |
| Any schema drift fails loudly                      | ✅ the frozen vector test.                                        |
