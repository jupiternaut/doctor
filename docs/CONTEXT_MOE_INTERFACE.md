# Context MoE Interface

Doctor treats the local machine as a set of context experts, not as one flat
Markdown dump. Documents, code, videos, sessions, user profiles, and workflows
can keep their best specialized indexes, but every provider must emit the same
canonical evidence shape before it enters the resolver and hot pack.

## Principle

```text
raw source
  -> provider-specific extraction/index
  -> GrepRouteProbe L0/L1 gate
  -> EvidenceRecord
  -> multi-index retrieval
  -> fusion/rerank
  -> hot context pack
```

The shared unit is not a vector. The shared unit is an `EvidenceRecord`.

Different modalities may use different embeddings and indexes:

- documents: text chunks, FTS, text embeddings, GraphRAG-style relations
- code: code graph, symbols, call edges, code embeddings
- video: metadata, OCR, ASR, frame/vision embeddings, watch behavior
- sessions: temporal messages, decisions, working summaries
- profiles: preference facts, feedback edges, user behavior weights

Doctor's job is to normalize these records, route between them, and package the
small set that should be activated for the current task.

## GrepRouteProbe

`grep` / `ripgrep` is a first-class routing signal, not just a text-search
utility. Doctor uses it as a deterministic L0/L1 gate before semantic retrieval:

```text
goal
  -> query terms and entity expansions
  -> ripgrep over manifests / extracted Markdown / indexed chunks / provider cards
  -> provider_scores
  -> resolver selected_sources
  -> small final rerank boost for matching provider/path
```

This makes exact local signals cheap and explainable:

- file names, paths, project names
- function names, classes, config keys, error logs
- people, products, titles, tags, Markdown KV fields
- Douyin video metadata after it has been converted into Markdown KV

The route probe does not replace embeddings, code graphs, OCR, ASR, or
feedback. It decides which experts should wake up first, then contributes a
small explainable `resolver_score_parts.grep_route` boost during final fusion.
The selected experts then return `EvidenceRecord` candidates for hot-pack
generation.

## EvidenceRecord

Current schema version: `0.1`.

```json
{
  "schema_version": "0.1",
  "evidence_id": "source-or-chunk-id",
  "source_type": "code|document|image|video|audio|session|profile|workflow|project|artifact|unknown",
  "source_group": "git_repositories",
  "provider": "codebase_memory",
  "path": "/absolute/source/path",
  "relative_path": "src/app.py",
  "title": "human readable title",
  "text": "compact searchable text",
  "summary": "short summary or snippet",
  "quote": "small source quote for hot packs",
  "location": {
    "line": 42,
    "timestamp": 12.4
  },
  "score": 0.72,
  "score_parts": {},
  "retrieval": {
    "query": "task query",
    "matched_queries": ["expanded query"],
    "channels": ["semantic_index", "codebase_memory_search_code"]
  },
  "identifiers": {
    "doc_id": "...",
    "source_id": "...",
    "source_chunk_id": "..."
  },
  "entities": [],
  "edges": [],
  "embedding_refs": [
    {"kind": "text", "ref": "derived:text"},
    {"kind": "code", "ref": "provider:code_graph"}
  ],
  "permissions": {},
  "provenance": {}
}
```

## Why Not One Vector

One vector space is too lossy for Doctor's target shape. A Python call graph, a
PDF paragraph, a video frame, and a Douyin preference event are not the same
kind of evidence.

Doctor instead uses:

```text
unified EvidenceRecord
  + specialized embeddings/indexes
  + grep route probe
  + late fusion
  + feedback-updated edge weights
```

This lets the resolver ask:

- Is this a code task?
- Is this a document research task?
- Does the task need user-profile behavior?
- Does a video transcript or OCR result matter?
- Should multiple routes be shown for user choice?

## Provider Contract

Every provider may keep its own storage and retrieval implementation, but it
must be able to project results into `EvidenceRecord`.

| Provider | Native strength | Evidence source type |
| --- | --- | --- |
| Downloads / MarkItDown | document text extraction | `document` |
| `codebase-memory-mcp` | code graph and symbol search | `code` |
| project index | project files and README/docs/code chunks | `code` / `project` |
| Codex/Claude sessions | temporal working memory | `session` |
| workflow docs | reusable task protocols | `workflow` |
| Douyin provider | videos, authors, watch/profile signals | `video` / `profile` |

## Current Implementation

`src/agent_context/evidence.py` maps legacy source dictionaries into canonical
evidence records.

`src/agent_context/evidence_index.py` builds `indexes/evidence.sqlite` from
provider manifests, resolver packs, and query packs. The index stores
`evidence_nodes` and `evidence_edges` while keeping the full canonical record in
`payload_json`.

`src/agent_context/grep_route.py` runs the L0/L1 route probe. Resolver plans now
include `grep_route_probe.provider_scores`, and `context.md` reports the top
provider scores when deterministic local matches are found. Final sources also
include `resolver_score_parts.grep_route`, `grep_route_provider_score`, and
`grep_route_hits`.

CLI entrypoints:

```bash
agent-context evidence-index --out /Users/gengrf/agent-context-system
agent-context evidence-search --out /Users/gengrf/agent-context-system --query "feedback rerank"
```

The following outputs now include an `evidence` field per source:

- resolver hot packs: `packs/<task-id>/sources.jsonl`
- v0.1 build packs: `packs/<task-id>/sources.jsonl`
- cold-index query packs: `queries/<query-id>/sources.jsonl`

Legacy source fields remain in place for MCP, arena, feedback, and existing
tests. The canonical record is an additive compatibility layer.

## Next Steps

- Add provider-native `entities` and `edges` instead of leaving them empty.
- Use `indexes/evidence.sqlite` as a resolver retrieval provider instead of only
  a standalone query surface.
- Add route-level feedback edges: selected, rejected, corrected, stale.
- Add MediaProvider records for OCR, ASR, keyframes, and profile signals.
- Add a router eval set that checks whether code, document, session, and video
  evidence are activated for the right task.
