# Outstanding tasks — 2026-06-23

## Waiting for merge (Claude/maintainer)

The following PRs have been reviewed and approved by febuz; they are ready to
merge into `main`:

- **#248** — `feat(lens)`: MeTTa-inspired atomspace + adapter for virtualpc agents
- **#249** — `feat(pouw)`: quorum-aware settlement for useful work
- **#251** — `docs(tools)`: migration plan PDF generator for Knitweb/pulse
- **#263** — `feat(synaptic)`: fiber taxonomy for semantic bundle categorisation

Action: merge by maintainer/Claude. No further code changes required.

---

## Next development priorities

### 1. Body of Knowledge ingestion pipeline (Phase 2)
Build a bulk ingestion tool that turns raw sources into tagged Fiber bundles.

- Sources (first batch): DAMA-DMBOK chapters, PubChem subset, ArXiv abstracts,
  RationalWiki/Skeptoid summaries.
- Output: `compile_bundle()` bytes with `hasFiber` / `hasDomain` metadata
  relations from `knitweb.synaptic.fiber`.
- Target script: `tools/bulk_ingest.py`
- Acceptance: `python tools/bulk_ingest.py --source-dir ...` produces bundles
  that can be loaded into `LensSpace` via `KnitwebLensAdapter`.

### 2. Agent Army orchestration
Design the server-side orchestrator for specialist analyst agents.

- Hardware target: 2× Xeon Platinum 8276L, 6 TB RAM, 2× RTX 3090 NVLinked.
- Control endpoint: MacBook Air 2023 (remote HTTP / P2P).
- Agent roles: Data Governance, Data Quality, Metadata, Chemistry, Physics,
  Pseudo-Science Auditor.
- Each agent: fiber/domain specialisation, LLM endpoint, certification target.

### 3. Certification test generator
Generate DAMA-DMBOK-style tests from ingested triples and mint certificates.

- Transform subject/predicate/object relations into Q&A items.
- Auto-grade agents; store passing certificates as `certification` fiber bundles.
- Levels: Trainee / Practitioner / Certified Professional.

### 4. "Last Humanity Test" A/B demo
Two identical models, one with Lens+P2P and one without, answering the same
hard multi-domain questions. Dashboard shows correctness, provenance and
confidence differences.

---

## Notes

- Do not commit generated PDFs to the repo; keep them in `.gitignore`.
- Keep "VirtuAnalytica" naming out of public repo files; use "VirtualPC Demo".
