## Context

Doctor already has ingestion, manifests, extracted Markdown, SQLite/FTS indexes, semantic refresh, resolver packs, MCP tools, access policy, feedback, and runtime review surfaces. The missing layer is not another search backend; it is a durable LLM-Wiki/OKF vault that turns raw local evidence into reviewed, human-readable knowledge pages.

The user rejected a toy Mirror-first architecture and asked whether OKF could replace Mirror. The correct boundary is:

```text
Raw files are read-only evidence.
LLM-Wiki/OKF vault is canonical curated knowledge.
SQLite/FTS/vector/graph indexes are rebuildable derived state.
Runtime resolver and hot packs read the vault and indexes on demand.
Mirror-style signals become feedback/ranking priors, not the canonical store.
```

The scale boundary is explicit: `/Users/gengrf` has millions of files, so a one-card-per-file or whole-vault prompt is not feasible. The baseline must support progressive disclosure, query-time retrieval, and reviewable AI writes.

## Goals / Non-Goals

**Goals:**

- Establish a Doctor knowledge baseline that is closer to LLM-Wiki than RAG.
- Make OKF-compatible Markdown concept pages the canonical knowledge layer.
- Keep original local files immutable and referenced by path/hash/source metadata.
- Treat databases and vector stores as disposable acceleration layers.
- Require Brain Diff-style human review for AI-maintained knowledge updates.
- Add stable entity identity, contradiction nodes, stale/drift metadata, and failure-path memory as first-class concepts.
- Define baseline commands and reports that prove the architecture works before expanding full media parsing.

**Non-Goals:**

- Do not attempt to feed the whole OKF vault into one model call.
- Do not replace the existing Doctor Runtime change.
- Do not require Mirror to survive as a standalone product.
- Do not make AI writes silently modify canonical knowledge.
- Do not solve every parser type, OCR path, ASR path, or archive expansion policy in this change.

## Decisions

### Decision 1: Vault is canonical; indexes are throwaway

Doctor SHALL store curated knowledge as Markdown/OKF files under a vault directory. SQLite, FTS, vector tables, graph edge files, and route priors are derived from that vault and can be deleted and rebuilt.

Alternative considered: store canonical knowledge directly in SQLite/PostgreSQL. Rejected because the user needs human-readable review, Git diff, manual edits, and long-term portability.

### Decision 2: AI writes are staged as diffs, not direct commits

An ingest or compile run SHALL write proposed knowledge changes into a diff/staging directory. The user or an approved runner must accept the diff before canonical vault files change.

Alternative considered: allow the agent to update vault pages directly. Rejected because hallucinated summaries, stale claims, and entity merges must be reviewable.

### Decision 3: Entity identity is separate from display names

Doctor SHALL assign stable entity IDs and aliases. Display names such as `Codex`, `Doctor`, `PLM`, or `Gugu` are not enough to identify concepts.

Alternative considered: use filename/title as identity. Rejected because aliases, renamed projects, and homonyms cause taxonomy drift.

### Decision 4: Contradictions and failures are knowledge objects

Contradictions and failed paths SHALL become explicit concept pages rather than hidden metadata. This preserves uncertainty and prevents future agents from repeating rejected routes.

Alternative considered: lower the source score or overwrite old claims. Rejected because conflict history and dead ends are useful context for later reasoning.

### Decision 5: Baseline retrieval is progressive disclosure, not full-vault context

Doctor SHALL expose top-level and subdirectory indexes for browsing, but online tasks SHALL query derived indexes and open only bounded concept sets.

Alternative considered: generate a complete `model_input.md` for the entire vault. Rejected because even minimal whole-home summaries exceed practical context windows by orders of magnitude.

### Decision 6: Mirror becomes feedback, not source of truth

Mirror-style labels and ranking weights SHALL be importable as feedback signals, but they SHALL NOT be the canonical source of project truth. Project truth lives in vault concept pages with citations.

Alternative considered: keep Mirror as the main personal memory layer. Rejected because it remains ranking-heavy and does not provide durable knowledge governance.

## Risks / Trade-offs

- [Risk] The vault creates more files and review overhead. -> Mitigation: start with project/entity/workflow concepts and only generate file-level concepts for selected sources.
- [Risk] AI-generated summaries may look authoritative. -> Mitigation: require citations, source hashes, diff staging, and contradiction handling.
- [Risk] Derived indexes may drift from Markdown. -> Mitigation: use content hashes, index metadata, model identity guards, and rebuild commands.
- [Risk] Entity IDs and ontology rules may slow early development. -> Mitigation: start with a small seeded ontology and allow aliases to expand after review.
- [Risk] The baseline may become another document generator without runtime value. -> Mitigation: require query and hot-context tests that compare vault retrieval against current context packs.

## Migration Plan

1. Add the vault directory contract and concept templates without changing raw-file ingestion behavior.
2. Add a baseline compiler that converts selected existing Doctor evidence into proposed OKF/LLM-Wiki pages.
3. Add diff staging and approval artifacts before writing canonical vault pages.
4. Add a derived knowledge index that reads approved vault pages and writes SQLite/FTS/vector/edge metadata.
5. Add a baseline report that measures vault size, index size, query results, full-context infeasibility, and hot-context feasibility.
6. Wire resolver reads to the vault/index as an optional provider before changing default resolver behavior.

Rollback strategy: delete or ignore `vault/`, `vault/diffs/`, and derived `indexes/knowledge*` artifacts. Original source files, existing manifests, existing packs, and current runtime commands remain untouched.

## Open Questions

- Should the canonical vault live at `vault/` or `okf/doctor-vault/`?
- Should OKF validation use the Google reference parser as a vendored dependency, a local optional dependency, or a compatibility test only?
- Which model or local workflow is allowed to draft knowledge diffs?
- Should vault approval use Git commits, explicit `doctor wiki approve`, or both?
- How much of the existing Mirror feedback should be imported into the first vault baseline?
