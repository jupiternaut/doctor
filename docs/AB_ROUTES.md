# A/B Context Routes

This document defines the first executable A/B experiment for
`agent-context-system`.

The goal is to compare two context delivery routes on the same scope and task
goal before committing to one architecture.

## Route A: Chunk Pack

Route A is the current v0.1 path.

```text
files
  -> extracted Markdown
  -> chunks.jsonl
  -> keyword/path ranking
  -> context.md with direct snippets
```

It answers:

```text
Which extracted passages should Codex read right now?
```

Strengths:

- Direct source snippets.
- Simple deterministic scoring.
- Good for tasks where quotes and concrete file paths matter.

Limits:

- Weak global orientation.
- No explicit graph of folders, file types, concepts, or repeated matches.
- No recall or edge-weight feedback yet.

## Route B: Graph Context Map

Route B is a graph-lite experiment inspired by Understand Anything and
OpenClaw's memory direction, but scoped to mixed local files.

```text
files
  -> extracted Markdown + manifests
  -> graph-lite nodes and edges
  -> document ranking through context map
  -> context.md with source map + snippets
```

Current graph-lite nodes:

```text
folder
extension
goal_term
document
```

Current graph-lite edges:

```text
contains
classifies
matches_goal
has_chunks
```

It answers:

```text
What source map should Codex understand before reading individual snippets?
```

Strengths:

- Better orientation over messy folders.
- Makes folder/type/term relationships inspectable.
- Provides a natural place to add recall events and edge-weight refresh.

Limits:

- It is not a full code AST.
- It does not replace Understand Anything for codebase architecture.
- It still uses deterministic local scoring, not embeddings or LLM summaries.

## Command

```bash
agent-context compare \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

Outputs:

```text
packs/<task-id>/context.md                 # Route A
packs/<task-id>-route-b-graph-*/context.md # Route B
packs/<task-id>-route-b-graph-*/context_graph.json
reports/ab_comparison_report.md
```

Use `--skip-ingest` to compare routes from existing manifests:

```bash
agent-context compare \
  --scope /Users/gengrf/Downloads \
  --goal "..." \
  --skip-ingest
```

## Reading The Result

Prefer Route A when the task needs direct evidence.

Prefer Route B when the task needs orientation, source coverage, and a place to
refresh relationship weights.

If both routes select the same path, inspect that path first. Agreement between
routes is the strongest local signal in this experiment.

## Next Integration Point

When the scanned scope is a git repository and
`.understand-anything/knowledge-graph.json` exists, Route B should import that
graph instead of only building a graph-lite map.

When recall events are implemented, both routes should write:

```json
{
  "goal": "...",
  "route": "a_chunk_pack",
  "path": "...",
  "score": 1.0,
  "selected": true,
  "reviewed": null,
  "created_at": "..."
}
```

Those events become the substrate for OpenClaw-style promotion and edge-weight
refresh.
