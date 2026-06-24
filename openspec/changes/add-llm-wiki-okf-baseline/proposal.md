## Why

Doctor currently behaves mostly like a local context runtime: it extracts files, builds indexes, resolves tasks, and emits bounded context packs. Recent LLM-Wiki and OKF research shows the missing baseline is a durable knowledge layer where raw local sources are compiled into human-reviewable Markdown/OKF concepts, with explicit provenance, conflicts, drift, and derived indexes.

This change creates a baseline contract for Doctor to become a local LLM-Wiki/OKF vault instead of only a search-and-pack tool. It also demotes Mirror from a primary architecture to a feedback/ranking layer on top of the vault.

## What Changes

- Introduce a canonical LLM-Wiki/OKF vault under Doctor output that stores AI-maintained knowledge pages separately from read-only raw files.
- Define OKF-compatible concept cards for projects, entities, workflows, sources, claims, contradictions, and failure paths.
- Define derived indexes as disposable state rebuilt from the vault: SQLite/FTS, vector index, graph edges, provider summaries, and route priors.
- Add governance requirements for Brain Diff-style review, stable entity IDs, contradiction nodes, stale/drift scoring, source citations, and write approval.
- Define a baseline evaluation that compares:
  - raw file search,
  - current Doctor context packs,
  - OKF/LLM-Wiki vault retrieval,
  - and full-vault context-size feasibility.
- Keep existing Doctor Runtime phases as clients of this knowledge layer; do not replace the runtime change.
- Non-goals:
  - Do not attempt to put the whole OKF vault into one model context.
  - Do not mutate original files.
  - Do not require a perfect full-disk media parser before the vault baseline is useful.
  - Do not make Mirror the canonical knowledge store.

## Capabilities

### New Capabilities

- `llm-wiki-okf-vault`: Defines the canonical Markdown/OKF vault, concept types, source provenance, and sidecar storage model.
- `derived-knowledge-index`: Defines rebuildable SQLite/FTS/vector/graph indexes over the vault and the integrity contract that keeps them aligned with canonical Markdown.
- `knowledge-governance`: Defines reviewable AI writes, Brain Diffs, stable entity identity, contradiction handling, stale/drift detection, and failure-path memory.

### Modified Capabilities

None.

## Impact

- Affected local project: `/Users/gengrf/agent-context-system`
- Affected product architecture:
  - Doctor becomes the canonical local knowledge compiler and runtime.
  - OKF becomes the long-term knowledge interchange and directory format.
  - Mirror becomes a user preference, feedback, and ranking signal layer rather than a primary context store.
- Affected future artifacts:
  - `vault/index.md`
  - `vault/log.md`
  - `vault/projects/*.md`
  - `vault/entities/*.md`
  - `vault/workflows/*.md`
  - `vault/sources/*.md`
  - `vault/claims/*.md`
  - `vault/contradictions/*.md`
  - `vault/failures/*.md`
  - `vault/diffs/<run-id>/`
  - `indexes/knowledge.sqlite`
  - `indexes/knowledge_vectors.*`
  - `indexes/knowledge_edges.jsonl`
  - `reports/llm_wiki_baseline_report.md`
- Affected commands or future command shape:
  - `doctor wiki ingest`
  - `doctor wiki compile`
  - `doctor wiki diff`
  - `doctor wiki approve`
  - `doctor wiki index`
  - `doctor wiki query`
  - `doctor wiki baseline`
- Dependencies:
  - Existing file ingestion and extraction.
  - Existing Doctor resolver and context pack builder.
  - Existing OKF research clone and local OKF adapter work.
  - Existing feedback model and access policy.
