# Context Router Mainstream Framework

This document defines the mainstream architecture for turning a user task into
an agent-readable context pack.

The goal is not to build another "chat with documents" UI. The goal is:

```text
user task goal
  -> choose the right local sources
  -> retrieve and rerank evidence
  -> write a small hot context pack
  -> let Codex continue the task
```

## 1. Mainstream Pattern

The common industry pattern is:

```text
Source Registry
  -> Query / Intent Router
  -> Query Planner
  -> Hybrid Retrieval
  -> Fusion + Rerank
  -> Context Pack Builder
  -> Agent Tool / MCP Interface
  -> Feedback Loop
```

Equivalent names in existing systems:

- LlamaIndex: router retriever / router query engine.
- LangChain: self-query retriever and structured query construction.
- Haystack: conditional routers, metadata routers, text routers.
- Anthropic-style context engineering: retrieval, contextual BM25/embedding,
  reranking, and context selection.
- MCP / Agents SDK: expose search, read, and pack steps as tools.

## 2. Layer Responsibilities

### 2.1 Source Registry

The registry records what the system can search. It does not decide what to
search for a task.

Example sources:

```text
downloads_documents
git_repositories
codex_sessions
claude_sessions
workflow_docs
skills
project_handoffs
```

Each source should expose metadata:

```json
{
  "source_id": "downloads_documents",
  "kind": "documents",
  "scope": "/Users/gengrf/Downloads",
  "status": "indexed",
  "index_path": "indexes/context.sqlite",
  "strengths": ["pdf", "docx", "research notes", "downloaded references"],
  "weaknesses": ["not code-first", "may include stale files"]
}
```

### 2.2 Context Resolver

The resolver is the central module. It receives a task goal and outputs a
resolution plan.

Input:

```text
告诉我本地所有项目里如何构建个人推荐系统
```

Output:

```json
{
  "intent": "project_architecture_research",
  "entities": ["个人推荐系统"],
  "selected_sources": ["git_repositories", "codex_sessions", "downloads_documents"],
  "queries": [
    "个人推荐系统 架构",
    "recommendation system local project",
    "用户画像 推荐 排序 召回",
    "Codex 会话 推荐系统"
  ],
  "constraints": {
    "prefer_recent": true,
    "prefer_project_files": true,
    "max_context_tokens": 12000
  }
}
```

The first version should be rule-based and explainable. Use an LLM router later
only after the deterministic version is stable.

### 2.3 Query Planner

The planner expands one task goal into several retrieval queries and filters.

Typical outputs:

```text
keyword queries
semantic queries
metadata filters
source priority
time priority
file-type priority
```

For example, "本地所有项目" should add source filters for Git repositories and
agent session history. "PDF/资料/下载" should raise Downloads priority.

### 2.4 Hybrid Retrieval

Mainstream retrieval combines sparse, dense, and metadata signals:

```text
BM25 / FTS
  + vector embedding
  + path metadata
  + source metadata
  + time metadata
  + task-specific priors
```

Current project status:

```text
SQLite FTS5       implemented
hash-vector-lite  implemented
path priors       implemented
real embeddings   background fastembed semantic.sqlite implemented
ANN index         optional hnswlib ANN cache implemented; exact fallback remains default
```

### 2.5 Fusion And Rerank

Retrieval should over-fetch first, then rerank down to a small pack.

Recommended first implementation:

```text
1. Retrieve top 50-150 candidates.
2. Normalize scores per source.
3. Fuse by reciprocal rank or weighted score parts.
4. Apply source diversity so one file does not dominate.
5. Keep top 8-20 sources.
6. Write score_parts and explanation.
```

Later upgrades:

```text
cross-encoder reranker
LLM judge reranker
feedback-trained source priors
graph/community summaries
```

### 2.6 Context Pack Builder

The pack builder turns ranked evidence into something Codex can read directly.

Required files:

```text
packs/<task-id>/context.md
packs/<task-id>/sources.jsonl
packs/<task-id>/manifest.json
```

`context.md` should contain:

```text
task goal
resolution plan
must-know summary
top sources with paths
short quotes or snippets
limitations
recommended next action
```

`sources.jsonl` should keep machine-readable provenance:

```json
{
  "path": "/Users/gengrf/Downloads/example.pdf",
  "source_id": "downloads_documents",
  "score": 0.82,
  "score_parts": {
    "fts": 0.44,
    "vector": 0.31,
    "source_prior": 0.07
  },
  "why_selected": "matches recommendation-system terms and recent research path"
}
```

### 2.7 Agent Interface

Expose resolver through CLI and MCP.

CLI target:

```bash
agent-context resolve \
  --goal "告诉我本地所有项目里如何构建个人推荐系统"
```

MCP target:

```text
resolve_context(goal, limit=12)
```

Expected return:

```json
{
  "context_md_path": "packs/<task-id>/context.md",
  "sources_jsonl_path": "packs/<task-id>/sources.jsonl",
  "manifest_json_path": "packs/<task-id>/manifest.json",
  "selected_sources": ["git_repositories", "codex_sessions", "downloads_documents"]
}
```

### 2.8 Feedback Loop

Feedback should update routing and ranking, not just save comments.

Minimum feedback record:

```json
{
  "goal": "告诉我本地所有项目里如何构建个人推荐系统",
  "selected_source": "/path/to/source",
  "rating": 1,
  "reason": "actually useful for project architecture",
  "route": "rule_based_v0"
}
```

Use feedback to adjust:

```text
source priors
path priors
query expansion terms
rerank weights
```

## 3. Recommended MVP For This Repo

Do not start with a fully agentic loop. Build the controlled router first.

### v0.4 Contract

```bash
agent-context resolve \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --out /Users/gengrf/agent-context-system
```

Implemented outputs:

```text
packs/<task-id>-resolve-<timestamp>/context.md
packs/<task-id>-resolve-<timestamp>/sources.jsonl
packs/<task-id>-resolve-<timestamp>/manifest.json
packs/<task-id>-resolve-<timestamp>/resolution_plan.json
```

### v0.4 Scope

Use only what already exists plus a small registry:

```text
downloads_documents -> existing manifests/indexes/context.sqlite
git_repositories    -> metadata-only first, then code pack later
codex_sessions      -> metadata-only first, then JSONL parser later
workflow_docs       -> docs/*.md and PROJECT_TASK_README.md
```

### v0.4 Algorithm

```text
1. Parse goal with deterministic rules.
2. Classify intent:
   - project_code
   - document_research
   - agent_history
   - workflow_handoff
   - mixed
3. Select 1-3 source groups.
4. Generate 3-5 query strings.
5. Query existing cold index without writing intermediate `queries/*` packs.
6. Add source/path/task priors.
7. Rerank with diversity and write `why_selected`.
8. Write context pack and `resolution_plan.json`.
```

### v0.4 Non-Goals

Do not implement these in the first resolver:

```text
full disk scan
heavy GraphRAG
LLM-only routing
background daemon
GUI
perfect embeddings
automatic file mutation
```

## 4. How This Differs From RAG

RAG answers:

```text
Which chunks match this query?
```

Context Router answers:

```text
For this task, which sources should the agent inspect, why, and what compact
context should be placed in the current work session?
```

That means the router must be evaluated on task success, not only retrieval
similarity.

## 5. External References

- LlamaIndex Router Retriever:
  https://developers.llamaindex.ai/python/framework/integrations/retrievers/router_retriever/
- LlamaIndex RAG overview:
  https://developers.llamaindex.ai/python/framework/understanding/rag/
- LangChain SelfQueryRetriever:
  https://reference.langchain.com/python/langchain-classic/retrievers/self_query/base/SelfQueryRetriever
- Haystack Routers:
  https://docs.haystack.deepset.ai/docs/routers
- Anthropic Contextual Retrieval:
  https://www.anthropic.com/engineering/contextual-retrieval
- MCP specification:
  https://modelcontextprotocol.io/specification/2025-11-25
- OpenAI Agents SDK tools:
  https://openai.github.io/openai-agents-python/tools/
- Microsoft GraphRAG:
  https://microsoft.github.io/graphrag/
