# Outstanding tasks — 2026-06-23 (updated 2026-06-24)

## Merged today ✅

- **#248** — `feat(lens)`: MeTTa-inspired atomspace + adapter for virtualpc agents
- **#249** — `feat(pouw)`: quorum-aware settlement for useful work
- **#251** — `docs(tools)`: migration plan PDF generator for Knitweb/pulse
- **#263** — `feat(synaptic)`: fiber taxonomy for semantic bundle categorisation
- **#269** — `docs(backlog)`: outstanding tasks for agent army / demo

---

## Phase 2: Body of Knowledge Ingestion Pipeline (coordinated tasks)

> Detailed plan: `/Users/develuse/Admin/VirtualV Holding B.V./Virtuanalytica VOF/planning/PHASE2_PLAN.md`
> Hardware: agent army runs on server (2× Xeon Platinum 8276L, 6 TB RAM, 2× RTX 3090); MacBook Air 2023 controls remotely.

| ID | Task | Output | Owner | Status | Blocked by |
|----|------|--------|-------|--------|------------|
| P2-A | Source abstraction & format detection | `src/knitweb/ingest/source.py` + tests | Kimi | in progress | — |
| P2-B | Text extraction adapters (PDF/HTML/JSON/TXT) | `src/knitweb/ingest/extract.py` | Kimi | pending | P2-A |
| P2-C | Rule-based relation extraction | `src/knitweb/ingest/relations.py` | Kimi | pending | P2-B |
| P2-D | Fiber/domain tagger | `src/knitweb/ingest/tagger.py` | Kimi | pending | P2-C + fiber module |
| P2-E | Source → bundle compiler | `src/knitweb/ingest/compiler.py` | Kimi | pending | P2-D |
| P2-F | Bulk ingestion CLI | `tools/bulk_ingest.py` | Kimi | pending | P2-E |
| P2-G | Lens directory ingestion | extend `src/knitweb/lens/adapter.py` | Kimi | pending | P2-F + Lens PR merged |
| P2-H | Server-side ingestion runner | `deploy/server/ingest-runner.sh` | ops/future | pending | P2-F merged |
| P2-I | MacBook remote control hook | `scripts/remote_ingest.sh` | ops/future | pending | P2-H |

### P2-A acceptance criteria
- `Source` dataclass exists with path, format, fiber, domains, asset_cid,
  originator, metadata.
- `detect_format(path)` returns one of `pdf`, `html`, `json`, `txt`, `unknown`.
- `load_source(...)` validates fiber against `knitweb.synaptic.fiber.Fiber`.
- `pytest tests/property/test_ingest_source.py` passes.

### Source corpus (first batch)
- DAMA-DMBOK chapters → `data` fiber
- PubChem subset → `chem` fiber
- ArXiv abstracts → `academic` fiber
- RationalWiki/Skeptoid → `pseudo` fiber

---

## Later phases

### 3. Agent Army orchestration
Design server-side orchestrator; roles per fiber/domain; LLM endpoint scheduling.

### 4. Certification test generator
Generate Q&A from triples; auto-grade; mint `certification` fiber bundles.

### 5. "Last Humanity Test" A/B demo
Two identical models, one with Lens+P2P, one without; dashboard metrics.

---

## Notes

- Do not commit generated PDFs to the repo; keep them in `.gitignore`.
- Keep "VirtuAnalytica" naming out of public repo files; use "VirtualPC Demo".
