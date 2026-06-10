# File Ingestion Workflow

This workflow is the operating contract for v0.1 of `agent-context`.

It exists so that Codex, Codex Cloud, and other agents can scan local files
without modifying source material or confusing raw storage with agent-ready
context.

## Core Rule

Never modify files inside the scanned scope.

The scanner may read source files, hash them, extract text into this repository,
write manifests, write reports, and write context packs. It must not rewrite,
move, rename, annotate, delete, unzip, or normalize source files.

## Directory Contract

Default project output:

```text
extracted/
manifests/
reports/
packs/
```

Required files:

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

Generated private outputs stay out of git by default:

```text
extracted/
manifests/
packs/
```

Markdown reports may be committed when they are sanitized.

## Scanning Flow

1. Resolve the scope to an absolute path.
2. Walk files recursively.
3. Treat `.app` and `.pkg` directories as package metadata when encountered.
4. Compute size, mtime, SHA-256, extension, MIME guess, and relative path.
5. Choose parser policy by extension.
6. Skip re-extraction when path, size, mtime, hash, and parser version match a
   previous manifest record.
7. Write fresh JSONL manifests on each run.
8. Generate or regenerate the ingestion report.

## Parser Routing

Text-like files:

```text
.txt .md .markdown .csv .json .jsonl .xml .html .htm .yaml .yml
.py .js .ts .css .plist .strings .skill
```

Action: read directly as UTF-8 with replacement and a size cap.

Document files:

```text
.pdf .docx .xlsx .xls .pptx
```

Action: attempt MarkItDown first. If MarkItDown fails and Docling is installed,
try Docling as a fallback. Record failures in `manifests/failures.jsonl`.

Each document conversion runs in a child process with a default timeout:

```text
AGENT_CONTEXT_PARSER_TIMEOUT_SECONDS=45
```

If a file exceeds the timeout, record it as a recoverable failure and continue
the scan.

Images:

```text
.png .jpg .jpeg .webp .gif .heic
```

Action: metadata-only in v0.1.

Audio and video:

```text
.mp3 .wav .m4a .mp4 .mov
```

Action: metadata-only in v0.1.

Archives and packages:

```text
.zip .rar .7z .tar .gz .tgz .bz2 .xz .dmg .pkg .app .iso
```

Action: metadata-only. Do not expand.

Unknown or binary files:

```text
any other extension
```

Action: metadata-only.

## Archive Policy

Archives and package files are never expanded in v0.1.

The manifest should still record:

```text
path
relative_path
size_bytes
mtime
sha256
extension
mime
policy=metadata_only
parser=metadata_only
```

This preserves search visibility without creating hidden write amplification or
unexpected privacy exposure.

## Incremental Policy

A file is unchanged when all of these match the previous manifest:

```text
path
size_bytes
mtime
sha256
parser_version
```

Unchanged files should reuse previous extraction and chunks. The new manifest
may mark the record as `skipped` to prove incremental behavior happened.

## Manifest Policy

`documents.jsonl` is the document-level source of truth.

`chunks.jsonl` is the extracted-text search surface.

`failures.jsonl` is recoverable debt. It should exist even when empty.

Failures should not stop the whole scan.

## Context Pack Policy

The hot context pack is a task handoff, not a database dump.

It must include:

```text
Task
Must Know
Relevant Files
Extracted Facts
Source Quotes
Limitations
Recommended Next Actions
```

The pack should include short snippets and paths, not full documents.

v0.1 ranking is deterministic:

```text
goal terms
  -> chunk text overlap
  -> path overlap
  -> extracted-text preference
  -> stable sort
```

## QA Checklist

Before accepting a change:

```text
uv sync
uv run pytest -q
uv run ./agent-context build --scope fixtures/downloads_sample --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
uv run agent-context build --scope fixtures/downloads_sample --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

Then verify:

```text
source fixture files unchanged
archive.zip metadata-only
documents.jsonl exists
chunks.jsonl exists
failures.jsonl exists
downloads_ingestion_report.md exists
packs/*/context.md exists
packs/*/sources.jsonl exists
packs/*/manifest.json exists
second run skips unchanged files
```

## Recovery Rules

If extraction fails:

1. Record the failure.
2. Continue scanning.
3. Terminate timed-out document parser processes instead of blocking the scan.
4. Include failure counts in the report and context pack limitations.
5. Add Docling, OCR, or transcription only when the failure class justifies the
   added dependency.

If a pack is poor:

1. Inspect `sources.jsonl`.
2. Improve deterministic scoring first.
3. Add embeddings only after the JSONL and Markdown surfaces are stable.

## v0.1 Limitations

v0.1 does not include:

```text
OCR
audio/video transcription
archive expansion
vector index
MCP server
knowledge graph edge refresh
background watcher
full 1 TB benchmark
```
