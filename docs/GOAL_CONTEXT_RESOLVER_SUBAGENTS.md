# Goal: Context Resolver With Subagent Orchestration

Last updated: 2026-06-15 Asia/Shanghai

## 1. Goal

Build `agent-context resolve --goal` so the user can provide only a task goal and
receive a Codex-readable hot context pack.

Target command:

```bash
agent-context resolve \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --out /Users/gengrf/agent-context-system \
  --limit 12
```

Target output:

```text
packs/<task-id>-resolve-<timestamp>/
  context.md
  sources.jsonl
  manifest.json
  resolution_plan.json
```

This goal closes the current product gap:

```text
task goal
  -> choose sources
  -> plan queries
  -> retrieve candidates
  -> rerank with explanations
  -> write hot context pack
  -> expose through CLI and MCP
```

## 2. Current State

Implemented:

```text
agent-context build
agent-context ingest
agent-context pack
agent-context index
agent-context query
agent-context compare
agent-context arena
agent-context feedback
agent-context mcp
```

Local evidence:

```text
manifests/documents.jsonl: 1027 records
manifests/chunks.jsonl: 10931 records
manifests/failures.jsonl: 39 records
indexes/context.sqlite: local cold index
```

Implemented by v0.4 execution:

```text
agent-context resolve
resolve_context MCP tool
resolution_plan.json
source registry
task goal -> source selection
multi-query fusion and explainable rerank
```

Progress before execution:

```text
Context Resolver module: 5%
"goal only -> context pack" experience: 35-40%
usable personal context assistant: 35-40%
```

Progress after this goal:

```text
Context Resolver module: 70-75%
"goal only -> context pack" experience: 65-70%
usable personal context assistant: 60-65%
```

## 3. Non-Goals

Do not include these in v0.4:

```text
full disk scan
background daemon
GUI
remote HTTP server
automatic source-file mutation
archive expansion
OCR/audio/video transcription
foreground full-disk embedding rebuild
mandatory ANN dependency
cross-encoder reranker
LLM-only routing
heavy GraphRAG
automatic injection into every Codex session
```

v0.4 must remain local, deterministic, explainable, and testable.

## 4. Subagent Orchestration Model

The first implementation should use deterministic role workers in code. These
roles are designed to become real subagents later, but v0.4 should not require
multiple live LLM calls to work.

```text
Main Orchestrator
  -> Goal Parser
  -> Source Registry Auditor
  -> Query Planner
  -> Provider Retrievers
  -> Fusion / Reranker
  -> Context Pack Writer
  -> Verification / Feedback Recorder
```

### 4.1 Main Orchestrator

Responsibility:

```text
own resolve_context()
coordinate all workers
write final paths
return a compact JSON result
```

Input:

```text
goal
out_root
limit
```

Output:

```json
{
  "resolver_version": "0.4",
  "route": "rule_based_v0",
  "intent": "mixed",
  "selected_sources": ["downloads_documents", "workflow_docs"],
  "queries": ["..."],
  "context_md_path": ".../context.md",
  "sources_jsonl_path": ".../sources.jsonl",
  "manifest_json_path": ".../manifest.json",
  "resolution_plan_json_path": ".../resolution_plan.json",
  "sources_included": 12
}
```

### 4.2 Goal Parser

Responsibility:

```text
parse the user goal into intent, entities, keywords, constraints, and hints
```

Intent classes:

```text
project_code
document_research
agent_history
workflow_handoff
mixed
```

Output:

```json
{
  "intent": "mixed",
  "entities": ["个人推荐系统"],
  "keywords": ["个人推荐系统", "recommendation system", "架构", "构建"],
  "constraints": {
    "prefer_recent": true,
    "prefer_project_files": true,
    "max_context_sources": 12
  }
}
```

### 4.3 Source Registry Auditor

Responsibility:

```text
list available sources and decide which are usable for the goal
```

v0.4 source groups:

```text
downloads_documents -> existing manifests + indexes/context.sqlite
workflow_docs       -> docs/*.md and PROJECT_TASK_README.md
git_repositories    -> metadata-only first
codex_sessions      -> metadata-only first
```

Output:

```json
{
  "selected_sources": ["downloads_documents", "workflow_docs"],
  "source_reasons": {
    "downloads_documents": "existing cold index can retrieve document evidence",
    "workflow_docs": "goal asks about current architecture and implementation plan"
  },
  "source_candidates": [
    {
      "source_id": "downloads_documents",
      "status": "indexed",
      "index_path": "indexes/context.sqlite",
      "freshness": "known"
    }
  ]
}
```

### 4.4 Query Planner

Responsibility:

```text
expand one task goal into 3-5 retrieval queries and source filters
```

Example:

```json
{
  "queries": [
    "个人推荐系统 架构",
    "recommendation system local project",
    "用户画像 推荐 排序 召回",
    "Codex 会话 推荐系统"
  ],
  "filters": {
    "prefer_paths": ["docs", "README", "PROJECT_TASK_README", "src"],
    "prefer_extensions": [".md", ".py", ".ts", ".json"]
  },
  "refresh_plan": {
    "downloads_documents": "reuse_existing_index",
    "workflow_docs": "read_current_files"
  }
}
```

### 4.5 Provider Retrievers

Responsibility:

```text
retrieve candidates from each selected source
```

v0.4 should support:

```text
downloads retriever: reuse existing cold index
workflow docs retriever: scan docs/*.md, README.md, PROJECT_TASK_README.md
git repo retriever: metadata-only source candidates
codex sessions retriever: metadata-only source candidates
```

Candidate schema:

```json
{
  "source_id": "downloads_documents",
  "source_kind": "documents",
  "path": "/Users/gengrf/Downloads/example.pdf",
  "snippet": "...",
  "matched_queries": ["个人推荐系统 架构"],
  "score_parts": {
    "fts": 0.4,
    "vector": 0.31,
    "path": 0.11
  }
}
```

### 4.6 Fusion / Reranker

Responsibility:

```text
merge candidates, normalize scores, dedupe, preserve source diversity, and explain why each source was selected
```

Rules:

```text
over-fetch 50-150 candidates
normalize scores per source
combine fts/vector/path/source_prior/recency/feedback_prior
avoid one file or source dominating the pack
keep top 8-20 sources
write why_selected
```

Output:

```json
{
  "path": "/Users/gengrf/Downloads/example.pdf",
  "score": 0.82,
  "resolver_score_parts": {
    "retrieval": 0.55,
    "source_prior": 0.12,
    "diversity": 0.05,
    "feedback": 0.10
  },
  "why_selected": "matches recommendation-system terms and document source is relevant to research tasks"
}
```

### 4.7 Context Pack Writer

Responsibility:

```text
write context.md, sources.jsonl, manifest.json, and resolution_plan.json
```

`context.md` sections:

```text
# Task
# Resolution Plan
# Must Know
# Top Sources
# Source Notes
# Limitations
# Recommended Next Actions
```

### 4.8 Verification / Feedback Recorder

Responsibility:

```text
validate output contract and record later user feedback
```

Minimum feedback record:

```json
{
  "goal": "...",
  "route": "rule_based_v0",
  "selected_source": "...",
  "rating": 1,
  "reason": "useful for architecture decision"
}
```

## 5. File-Level Implementation Plan

### 5.1 Add `resolver.py`

Path:

```text
src/agent_context/resolver.py
```

Owns:

```text
resolve_context()
build_resolution_plan()
classify_goal()
plan_queries()
fuse_candidates()
render_resolver_context()
write_resolution_pack()
```

### 5.2 Modify `cold_index.py`

Path:

```text
src/agent_context/cold_index.py
```

Add a no-write search function:

```python
search_cold_index(out_root: Path, query: str, limit: int = 50) -> dict
```

Reason:

```text
Resolver should run multiple retrieval queries without creating many queries/* folders.
Existing query_cold_index() behavior must remain unchanged.
```

### 5.3 Modify `cli.py`

Path:

```text
src/agent_context/cli.py
```

Add:

```bash
agent-context resolve --goal "<task goal>" --limit 12 --out <out_root>
```

### 5.4 Modify `mcp_server.py`

Path:

```text
src/agent_context/mcp_server.py
```

Add:

```text
mcp_resolve_context(goal, limit=12, out_root=None)
resolve_context(goal, limit=12)
```

Difference from `search_context`:

```text
search_context: one query -> retrieved chunks -> query context pack
resolve_context: task goal -> plan -> multiple queries -> fused sources -> hot context pack
```

### 5.5 Add Tests

Path:

```text
tests/test_context_resolver.py
```

Required tests:

```text
test_resolve_cli_creates_resolution_pack
test_resolution_plan_is_rule_based_and_explainable
test_resolver_fuses_multiple_queries_without_query_pack_spam
test_mcp_resolve_context_returns_top_sources
```

### 5.6 Update Docs

Update:

```text
README.md
docs/MCP_SERVER.md
docs/CONTEXT_ROUTER_FRAMEWORK.md
```

## 6. Implementation Order

```text
1. Add search_cold_index() without changing query_cold_index().
2. Add resolver.py with rule-based plan, multi-query retrieval, fusion, and pack writing.
3. Add CLI resolve command.
4. Add MCP resolve_context tool.
5. Add resolver tests.
6. Update README and MCP docs.
7. Run full tests.
```

## 7. Acceptance Commands

Use a temporary output root first:

```bash
cd /Users/gengrf/agent-context-system

uv run pytest -q

rm -rf /tmp/agent-context-resolve-acceptance
uv run ./agent-context build \
  --scope fixtures/downloads_sample \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --out /tmp/agent-context-resolve-acceptance \
  --with-index

uv run ./agent-context resolve \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --out /tmp/agent-context-resolve-acceptance
```

## 8. Acceptance Criteria

The goal is accepted only when:

```text
context.md exists
sources.jsonl exists
manifest.json exists
resolution_plan.json exists
resolution_plan.json contains intent/entities/selected_sources/queries/constraints/refresh_plan
sources.jsonl records source_id/path/score/score_parts/why_selected
context.md is readable by Codex without extra chat context
fixture source file hashes are unchanged
resolve does not create many queries/* folders for internal query expansion
MCP resolve_context returns the same key paths and top sources
missing index behavior is explicit, not silent
uv run pytest -q passes
```

## 9. Subagent Execution Plan For Implementation

When implementation starts, use subagents with disjoint write scopes:

```text
Worker A: cold_index.py search_cold_index extraction
Worker B: resolver.py core plan/retrieve/fuse/write
Worker C: cli.py + mcp_server.py integration
Worker D: tests/test_context_resolver.py and docs updates
```

Coordination rules:

```text
Do not let multiple workers edit the same file.
Each worker must list changed files.
Main agent reviews and integrates all patches.
Full test run happens only after integration.
```

## 10. Progress Definition

Before implementation:

```text
Context Resolver module: 5%
```

After this goal is implemented and tests pass:

```text
Context Resolver module: 70-75%
"goal only -> context pack" experience: 65-70%
usable personal context assistant: 60-65%
```

Remaining after v0.4:

```text
real source providers for Git repos and Codex/Claude sessions
real embeddings / ANN / reranker
feedback-weight refresh
automatic Codex hook
UI / permissions / privacy policy
OCR / audio / video
```

## 11. Subagent Design Evidence

This goal was designed with three parallel subagents:

```text
Euclid  -> resolver architecture and scope boundary
Leibniz -> file-level implementation plan
Cicero  -> acceptance criteria and subagent orchestration
```

Integrated conclusion:

```text
Do not build another RAG query.
Build a deterministic Context Resolver that routes a task goal to sources,
queries, fused evidence, and a small hot context pack.
```
