# Cold Index And RAG v0.2

`agent-context` v0.2 adds a local cold query index on top of the v0.1 JSONL
manifests.

It is designed as the first usable RAG layer for local agent context, not as a
hosted "chat with documents" product.

## Storage Contract

```text
indexes/context.sqlite
queries/<query-id>-rag-<timestamp>/
  context.md
  sources.jsonl
  manifest.json
```

`indexes/context.sqlite` contains:

```text
documents       file-level DNS records
chunks          extracted text chunks
failures        parser failure records
chunks_fts      SQLite FTS5 full-text index when available
meta            index build metadata
```

Each chunk also stores a deterministic local hash-vector-lite embedding. This
lets the system do local hybrid retrieval without requiring an API key or model
download.

## Build

Build manifests, hot pack, and cold index in one command:

```bash
agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --with-index
```

Or build the cold index from existing manifests:

```bash
agent-context index
```

## Query

```bash
agent-context query \
  --query "哪些文件适合进入个人助手长期记忆" \
  --limit 12
```

The query writes a RAG context pack:

```text
queries/<query-id>-rag-<timestamp>/context.md
queries/<query-id>-rag-<timestamp>/sources.jsonl
queries/<query-id>-rag-<timestamp>/manifest.json
```

`context.md` is suitable for Codex to read directly. `sources.jsonl` is the
machine-readable retrieval result.

## Retrieval Mode

v0.2 uses hybrid local retrieval:

```text
SQLite FTS5 score
  + local hash-vector-lite cosine score
  + path/type metadata score
  + agent-asset prior for personal-assistant memory queries
  -> ranked sources
```

This is a real cold query index because it persists documents, chunks, failures,
FTS rows, and query outputs. It is still not a full neural RAG stack.

For queries such as "个人助手长期记忆", v0.2 adds a transparent path prior for
agent assets such as `SKILL.md`, workflow docs, context docs, handoff files, MCP
notes, and task-planner assets. This keeps the result aligned with the local
agent-memory use case without pretending to be semantic understanding.

## What It Is Not Yet

Not implemented in v0.2:

```text
remote embeddings
local embedding model
ANN index such as sqlite-vec/faiss/hnswlib
reranker model
MCP server
automatic Codex query hook
OCR/audio/video transcription
feedback-trained ranking
```

The current embedding is intentionally swappable. A future version can replace
`local-hash-vector-lite` with `sqlite-vec`, `faiss`, `hnswlib`, or a local
embedding model while keeping the same `query -> sources.jsonl -> context.md`
contract.
