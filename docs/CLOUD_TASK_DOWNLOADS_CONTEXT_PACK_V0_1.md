# Cloud Task: v0.1 Downloads End-to-End Context Pack

## Objective

Build v0.1 of a local-first `agent-context-system`.

The system must turn a file scope such as `/Users/gengrf/Downloads` into:

- extracted Markdown text
- JSONL manifests
- failure records
- an ingestion report
- a Codex-readable hot context pack

Minimum acceptance command:

```bash
agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

Cloud execution note:

- The cloud runner will not have access to `/Users/gengrf/Downloads` unless the folder is uploaded or mounted.
- In cloud, implement the system and verify it against a small fixture directory.
- The same command must run locally against the real Downloads folder without changing source files.

## Required Delivery Directory

Create or update:

```text
/Users/gengrf/agent-context-system/
  agent-context
  pyproject.toml
  README.md
  docs/
    FILE_INGESTION_WORKFLOW.md
    CLOUD_TASK_DOWNLOADS_CONTEXT_PACK_V0_1.md
  scripts/
  src/
    agent_context/
  tests/
  fixtures/
  extracted/
  manifests/
  reports/
  packs/
```

Use a Python implementation unless there is a strong reason not to. Keep it simple and runnable with `uv`.

## Non-Negotiable Rules

1. Do not modify source files under the scanned scope.
2. Do not expand archive/package files for v0.1.
3. Archive/package files must be indexed by filename, path, size, hash, and type only.
4. PDF, DOCX, XLSX, and PPTX files must be attempted for Markdown extraction.
5. Extraction failures must be written to `manifests/failures.jsonl`.
6. Re-running must be incremental: if path, size, mtime, hash, and parser version are unchanged, skip re-extraction.
7. Always generate a hot context pack.
8. The generated `context.md` must be directly readable by Codex and include paths, summaries, quotes/snippets, limitations, and next actions.

## Required GitHub Reuse Check

Before implementing each major module, inspect current GitHub projects and reuse libraries or design patterns where they fit.

Document the result in:

```text
reports/github_reuse_report.md
```

For each candidate, record:

- repository URL
- license
- what it solves
- whether it was used directly, used as design reference, or rejected
- reason

Required candidates to check:

### Document Conversion

- `microsoft/markitdown`
  - URL: https://github.com/microsoft/markitdown
  - Reason: lightweight Python tool for converting files and Office documents to Markdown.
  - Known capabilities to verify: PDF, PowerPoint, Word, Excel, images metadata/OCR, audio metadata/transcription, HTML, CSV/JSON/XML, ZIP, EPUB.
  - Important constraint: v0.1 must not expand archives even if MarkItDown can.

- `docling-project/docling`
  - URL: https://github.com/docling-project/docling
  - Reason: document conversion and parsing for gen-AI workflows, stronger on PDF/layout/table cases.
  - Use as fallback or design reference if installing it is too heavy.

- `apache/tika`
  - URL: https://github.com/apache/tika
  - Reason: broad file type detection and text extraction fallback.
  - Use only if it does not make the v0.1 setup too heavy.

### Context Pack / AI-Friendly Packaging

- `yamadashy/repomix`
  - URL: https://github.com/yamadashy/repomix
  - Reason: packs repositories into AI-friendly Markdown/XML/JSON/plain text and supports MCP.
  - Use as design reference for context pack structure, not as a direct dependency unless it fits non-code documents.

### MCP / Future Interface

- `modelcontextprotocol/servers`
  - URL: https://github.com/modelcontextprotocol/servers
  - Reason: reference MCP server implementations.
  - v0.1 does not need a working MCP server, but design the CLI and storage so v0.2 can expose MCP tools.

## v0.1 Scope

### File Types

Implement these policies:

```text
Text-like files:
  .txt .md .markdown .csv .json .jsonl .xml .html .htm .yaml .yml .py .js .ts .css .plist .strings .skill
  -> read directly as text with size limits

Document files:
  .pdf .docx .xlsx .xls .pptx
  -> attempt Markdown conversion with MarkItDown first
  -> optionally fallback to Docling if installed

Images:
  .png .jpg .jpeg .webp .gif .heic
  -> v0.1 metadata only unless MarkItDown extraction is cheap and local

Audio/video:
  .mp3 .wav .m4a .mp4 .mov
  -> v0.1 metadata only

Archives/packages:
  .zip .rar .7z .tar .gz .dmg .pkg .app .iso
  -> metadata only, never expand

Unknown/binary:
  -> metadata only
```

### Required Outputs

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

### Required JSONL Schemas

`manifests/documents.jsonl`:

```json
{
  "doc_id": "sha256:<hash>",
  "path": "/absolute/path",
  "relative_path": "path/inside/scope",
  "scope": "/absolute/scope",
  "size_bytes": 123,
  "mtime": "2026-06-10T00:00:00+08:00",
  "sha256": "<hash>",
  "extension": ".pdf",
  "mime": "application/pdf",
  "policy": "extract|metadata_only|skipped",
  "parser": "markitdown|direct_text|metadata_only|docling|none",
  "parser_version": "agent-context-v0.1",
  "status": "ok|failed|skipped",
  "extracted_md_path": "/absolute/path/or/null",
  "text_chars": 1000,
  "chunk_count": 3,
  "warnings": []
}
```

`manifests/chunks.jsonl`:

```json
{
  "chunk_id": "sha256:<file_hash>:0001",
  "doc_id": "sha256:<file_hash>",
  "path": "/absolute/path",
  "chunk_index": 1,
  "text": "chunk text",
  "char_start": 0,
  "char_end": 1200,
  "token_estimate": 300
}
```

`manifests/failures.jsonl`:

```json
{
  "path": "/absolute/path",
  "sha256": "<hash>",
  "stage": "hash|extract|chunk|pack",
  "parser": "markitdown",
  "error_type": "ExceptionClass",
  "error": "short error",
  "recoverable": true
}
```

`packs/<task-id>/manifest.json`:

```json
{
  "context_pack_version": "0.1",
  "task_id": "slug-or-timestamp",
  "goal": "user goal",
  "scope": "/absolute/scope",
  "created_at": "2026-06-10T00:00:00+08:00",
  "documents_considered": 100,
  "sources_included": 10,
  "context_md_path": "/absolute/path",
  "sources_jsonl_path": "/absolute/path"
}
```

## Context Pack Requirements

`context.md` must include:

```markdown
---
context_pack_version: 0.1
goal: ...
scope: ...
created_at: ...
---

# Task

# Must Know

# Relevant Files

# Extracted Facts

# Source Quotes

# Limitations

# Recommended Next Actions
```

Packing algorithm for v0.1:

1. Build simple query terms from the goal.
2. Score chunks by keyword overlap, path/title overlap, recency, and file type.
3. Prefer extracted text over metadata-only files.
4. Include no more than 20 sources.
5. Include short snippets only, not full documents.
6. Always disclose metadata-only sources and extraction failures.

No LLM is required for v0.1. Use deterministic summarization/snippet extraction.

## Workflow Document Requirement

Write:

```text
docs/FILE_INGESTION_WORKFLOW.md
```

Model it after:

```text
/Users/gengrf/pet-asset-library/docs/IMAGE_GENERATION_WORKFLOW.md
```

It must cover:

- core rule
- directory contract
- scanning flow
- parser routing
- archive policy
- incremental policy
- output manifest policy
- context pack policy
- QA checklist
- recovery rules
- current v0.1 limitations

## CLI Requirements

Support:

```bash
agent-context build --scope <path> --goal <text>
agent-context ingest --scope <path>
agent-context pack --scope <path> --goal <text>
agent-context report --scope <path>
```

`build` must run `ingest` then `pack`.

The CLI should default output root to the project directory, but allow:

```bash
--out /custom/output/root
```

## Test Requirements

Create small fixtures:

```text
fixtures/downloads_sample/
  notes.md
  task-planner.skill
  data.json
  archive.zip
```

If possible, include one tiny generated DOCX/XLSX/PPTX/PDF fixture or create them during tests using lightweight Python libraries. If dependency cost is too high, skip generated binary fixtures but keep tests for parser routing.

Tests must verify:

- source files are not modified
- archives are metadata-only and not expanded
- JSONL files are created
- failures are recorded for unsupported/bad files
- extracted Markdown is created for text-like files
- context pack is generated
- rerun skips unchanged files

## Acceptance Checklist

The cloud task is complete only when all are true:

1. `agent-context build --scope fixtures/downloads_sample --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"` succeeds.
2. `manifests/documents.jsonl` exists and contains records.
3. `manifests/chunks.jsonl` exists and contains chunks for text-like fixtures.
4. `manifests/failures.jsonl` exists even if empty.
5. `extracted/*.md` exists for text-like extracted files.
6. `reports/downloads_ingestion_report.md` exists.
7. `packs/<task-id>/context.md` exists.
8. `packs/<task-id>/sources.jsonl` exists.
9. `packs/<task-id>/manifest.json` exists.
10. A second run skips unchanged files.
11. The implementation does not write into the scanned source directory.
12. `reports/github_reuse_report.md` records the required GitHub reuse check.

## Local Real-Data Follow-Up

After cloud implementation is returned, run locally:

```bash
cd /Users/gengrf/agent-context-system
uv sync
./agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

Expected local outputs:

```text
/Users/gengrf/agent-context-system/manifests/documents.jsonl
/Users/gengrf/agent-context-system/manifests/chunks.jsonl
/Users/gengrf/agent-context-system/manifests/failures.jsonl
/Users/gengrf/agent-context-system/reports/downloads_ingestion_report.md
/Users/gengrf/agent-context-system/packs/<task-id>/context.md
```

## Out of Scope for v0.1

- Full 1T scan
- Vector index
- MCP server
- OCR with PaddleOCR
- Whisper audio/video transcription
- Edge-weight refresh
- Knowledge graph relation extraction
- Archive expansion
- Background file watcher

