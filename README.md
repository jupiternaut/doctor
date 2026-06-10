# Agent Context System

Local-first context infrastructure for personal agents.

This repository captures the design and execution plan for turning a personal
file system into an agent-usable context layer:

```text
raw files
  -> document extraction
  -> cold index
  -> context DNS / resolver
  -> ranking and edge refresh
  -> hot context pack
  -> Codex / automation
  -> writeback
```

## Documents

- [Architecture Context](docs/ARCHITECTURE_CONTEXT.md)
- [Cloud Task: Downloads Context Pack v0.1](docs/CLOUD_TASK_DOWNLOADS_CONTEXT_PACK_V0_1.md)
- [Agent Context System Handoff](docs/HANDOFF.md)
- [File Ingestion Workflow](docs/FILE_INGESTION_WORKFLOW.md)
- [Cold Index And RAG](docs/COLD_INDEX_RAG.md)
- [A/B Context Routes](docs/AB_ROUTES.md)
- [Arena Evaluation](docs/ARENA.md)
- [GitHub Reuse Report](reports/github_reuse_report.md)

## Current Status

This repository currently contains the technical design, local experiment
results, a cloud-executable implementation task, and the v0.1 local CLI.

The local proof of concept used Basic Memory to index `/Users/gengrf/Downloads`
as a small cold-index experiment. The CLI now supports v0.1 document ingestion
and hot context packs plus a v0.2 local cold index/RAG slice using SQLite, FTS5,
and deterministic local hash-vector-lite retrieval. MCP integration, OCR,
audio/video transcription, neural embeddings, ANN vector search, and
edge-weight refresh are not implemented yet.

## Setup

```bash
uv sync
```

## Fixture Validation

```bash
uv run pytest -q
uv run ./agent-context build \
  --scope fixtures/downloads_sample \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --with-index
uv run agent-context build \
  --scope fixtures/downloads_sample \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --with-index
uv run ./agent-context query \
  --query "task planner skill workflow" \
  --out . \
  --limit 5
```

## v0.1 Acceptance Target

```bash
agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

Local helper:

```bash
scripts/local_downloads_build.sh
```

The command should produce:

```text
manifests/documents.jsonl
manifests/chunks.jsonl
manifests/failures.jsonl
extracted/<file_hash>.md
reports/downloads_ingestion_report.md
packs/<task-id>/context.md
packs/<task-id>/sources.jsonl
packs/<task-id>/manifest.json
```

## v0.2 Cold Index And RAG

```bash
agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --with-index
```

To query existing manifests/indexes:

```bash
agent-context index
agent-context query \
  --query "哪些文件适合进入个人助手长期记忆" \
  --limit 12
```

The command writes:

```text
indexes/context.sqlite
queries/<query-id>-rag-<timestamp>/context.md
queries/<query-id>-rag-<timestamp>/sources.jsonl
queries/<query-id>-rag-<timestamp>/manifest.json
```

## A/B Route Experiment

```bash
agent-context compare \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

Route A is the existing chunk/snippet hot pack. Route B is a graph-lite context
map that makes folder, file type, goal term, document, and chunk relationships
explicit before ranking sources. The comparison report is written to:

```text
reports/ab_comparison_report.md
```

## Arena Evaluation

```bash
agent-context arena \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

Arena mode generates three route-specific answers, randomizes them as
`candidate-1`, `candidate-2`, and `candidate-3`, then records the user's chosen
candidate:

```bash
agent-context feedback \
  --slate packs/<task-id>-arena-<timestamp>/slate.json \
  --winner candidate-2 \
  --reason "best matches my intent"
```
