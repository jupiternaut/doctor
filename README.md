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

## Current Status

This repository currently contains the technical design, local experiment
results, and a cloud-executable implementation task for v0.1.

The local proof of concept used Basic Memory to index `/Users/gengrf/Downloads`
as a small cold-index experiment. Full document extraction, context pack
generation, MCP integration, and edge-weight refresh are not implemented yet.

## v0.1 Acceptance Target

```bash
agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
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
