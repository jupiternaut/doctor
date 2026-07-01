# Known Gaps

This file is intentionally blunt. It prevents new developers from confusing prototypes, generated evidence, and passing smoke tests with a finished product.

## Current Maturity Boundary

Doctor has a real local runtime foundation:

- Ingestion and generated manifests exist.
- Cold indexes and semantic maintenance exist.
- Resolver and hot context packs exist.
- MCP and runtime review surfaces exist.
- Mirror Lab can call the local Doctor backend through localhost.

But the latest checked acceptance evidence in `reports/v1-acceptance-latest.md` is `failed` and `ready=false`, with reproducibility evidence marked stale. Treat V1 as not release-accepted until a fresh acceptance run says otherwise.

## Core Product Risk

The most important metric is context activation quality.

Doctor/Mirror must answer:

```text
Given this user task, did the system activate the right local evidence?
```

Generating a valid `context.md` is necessary but not enough. The pack can be structurally correct and still contain the wrong sources.

## Mirror Personalization Is Incomplete

Mirror is not yet a finished recommendation system.

Known gap:

- For relevant tasks, the system should reliably prioritize user-core projects such as PLM, Drama, Codex++, and Gugu when appropriate.
- Current ranking and profile signals are still too weak to claim that Mirror "understands the user."
- Feedback can be recorded and replayed, but the full long-term learning loop is not yet proven in day-to-day use.

## Retrieval And Ranking Gaps

Current retrieval is useful but uneven:

- FTS and grep-like signals are strong for exact names, paths, functions, and terms.
- Semantic search exists through background index work, but ANN vector search is not a mature production path.
- Trained rerankers are not implemented.
- Route selection and feedback priors exist, but they need more curated cases and regression tests.
- Resolver source scopes can find evidence, but source prioritization still needs task-specific tuning.

## Media And Document Parsing Gaps

Incomplete or limited:

- OCR for arbitrary images.
- Audio/video transcription.
- Robust video scene/metadata understanding.
- Full archive expansion.
- Complex Office/PDF edge cases.
- Generated dependency-folder indexing.

Douyin provider work exists, but video understanding is not the same as a complete media intelligence layer.

## UI Productization Gaps

Current UI surfaces are local validation tools:

- Mirror Lab is Python-generated HTML/CSS/vanilla JS served by `ThreadingHTTPServer`.
- Runtime review server is also Python stdlib HTTP.
- There is no standalone React/Tauri/Electron/macOS product client in this repo.

If product UI becomes a priority, extract the API contract first and then build a dedicated client.

## Reproducibility Gaps

Known concerns:

- The worktree may contain uncommitted changes.
- Some reports can be stale.
- Generated data is local and may not be portable.
- Local paths can contain private evidence.

Before declaring a release:

```bash
uv run ./agent-context reproducibility-snapshot --out /Users/gengrf/agent-context-system
uv run ./agent-context v1-acceptance --out /Users/gengrf/agent-context-system --refresh-evidence
```

Then inspect the generated reports instead of trusting command exit alone.

## What Not To Claim Yet

Do not claim:

- "Doctor indexes the whole computer perfectly."
- "Mirror understands the user."
- "V1 is accepted."
- "The UI is production-ready."
- "Semantic search/rerank is solved."
- "Media understanding is complete."

Acceptable claim:

```text
Doctor is a working local context compiler with reviewable context packs.
Mirror is an early personal ranking and review layer.
The main remaining product risk is activating the right context consistently.
```

