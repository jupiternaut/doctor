# Agent Context System Handoff

Last updated: 2026-06-10 18:30 Asia/Shanghai

This is the operational handoff for the `agent-context-system` repository. It is
written for a future Codex, Codex Cloud, or open-source agent that needs to
continue the v0.1 Downloads context-pack work without relying on chat history.

## Current Local Branch Update

Branch:

```text
codex/v0.1-downloads-context-pack
```

The local checkout now contains a usable Python/uv CLI implementation plus the
A/B route experiment and Arena evaluation layer:

```text
agent-context build     # ingest + hot context pack
agent-context index     # JSONL manifests -> SQLite/FTS cold index
agent-context query     # cold index -> RAG query context pack
agent-context compare   # Route A chunk pack vs Route B graph-lite map
agent-context arena     # three randomized candidate answers for user choice
agent-context feedback  # append the user's selected candidate
```

Validated commands:

```bash
uv run pytest -q
uv run python -m compileall -q src tests
uv run ./agent-context build \
  --scope fixtures/downloads_sample \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --with-index
uv run ./agent-context query \
  --query "task planner skill workflow" \
  --out /tmp/agent-context-rag-fixture \
  --limit 5
uv run ./agent-context arena \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --skip-ingest
```

Latest real Downloads arena output:

```text
packs/downloads-arena-20260610182523/slate.md
packs/downloads-arena-20260610182523/slate.json
packs/downloads-arena-20260610182523/slate_key.json
packs/downloads-arena-20260610182523/candidate-1/
packs/downloads-arena-20260610182523/candidate-2/
packs/downloads-arena-20260610182523/candidate-3/
```

User-facing selection flow:

```bash
agent-context feedback \
  --slate packs/downloads-arena-20260610182523/slate.json \
  --winner candidate-2 \
  --reason "best matches my intent"
```

Read `slate.md` first. Do not open `slate_key.json` before choosing if the goal
is blind-ish route evaluation.

Current limitation: Arena v0.1 renders candidate answers locally from selected
sources. It does not yet run three independent Codex subprocess/API calls.

## v0.2 Cold Index / RAG Status

Implemented:

```text
indexes/context.sqlite
queries/<query-id>-rag-<timestamp>/context.md
queries/<query-id>-rag-<timestamp>/sources.jsonl
queries/<query-id>-rag-<timestamp>/manifest.json
```

The cold index stores document records, chunks, failures, SQLite FTS5 rows when
available, and deterministic local hash-vector-lite embeddings.

Validation commands:

```bash
cd /Users/gengrf/agent-context-system
uv run ./agent-context index
uv run ./agent-context query \
  --query "哪些文件适合进入个人助手长期记忆" \
  --limit 12
```

Still not implemented:

```text
neural embeddings
ANN vector index
reranking model
MCP server
automatic Codex query hook
feedback-trained edge refresh
```

## Repository

```text
repo: https://github.com/jupiternaut/agent-context-system
local checkout: /Users/gengrf/agent-context-system
default branch: main
visibility: private
```

Verified local baseline before this handoff branch:

```text
main commit: ee5c0f2 Add agent context system architecture docs
open GitHub PRs: none found by gh pr list
remote branches: origin/main only
```

## Scope Of This Handoff PR

This handoff PR is documentation-only. It records the repo state, Cloud task
state, verification commands, and next steps.

It is not the v0.1 implementation PR. The implementation PR must contain the
CLI, parser policies, tests, fixtures, and generated fixture validation outputs
described below.

## What Exists In GitHub

The current repository contains the design and cloud task package:

```text
README.md
docs/ARCHITECTURE_CONTEXT.md
docs/CLOUD_TASK_DOWNLOADS_CONTEXT_PACK_V0_1.md
reports/.gitkeep
.gitignore
```

These documents define the local-first context system:

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

The v0.1 acceptance target is:

```bash
agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

## What Is Not Yet In GitHub

The Python implementation described in the Cloud task is not present in GitHub
at the time this handoff was written.

Expected but not yet visible on GitHub:

```text
agent-context
pyproject.toml
src/agent_context/
tests/
fixtures/
docs/FILE_INGESTION_WORKFLOW.md
reports/github_reuse_report.md
scripts/local_downloads_build.sh
```

A Codex Cloud run reportedly produced an internal diff and summary, but local
GitHub verification did not find the claimed commit or PR:

```text
claimed commit: 793ac8d Implement v0.1 downloads context pack
claimed PR title: Implement v0.1 downloads context pack
actual gh pr list result: []
actual remote commits: ee5c0f2 only
```

Treat the Cloud implementation as not delivered until a real GitHub PR, branch,
or commit can be fetched.

## Cloud Task

Cloud task URL observed during the session:

```text
https://chatgpt.com/codex/cloud/tasks/task_e_6a28636115a48333902a46513212ec0b
```

Cloud task title observed:

```text
Implement v0.1 Downloads context pack
```

The Cloud UI showed a large internal diff and a visible `Create PR` button. If
that diff is still available, the next operator should create the GitHub PR from
the Cloud task page, then verify from the command line:

```bash
cd /Users/gengrf/agent-context-system
gh pr list \
  --repo jupiternaut/agent-context-system \
  --state all \
  --limit 20 \
  --json number,title,state,headRefName,baseRefName,url,updatedAt
```

Do not rely on the Cloud summary alone. The source of truth is GitHub plus the
local checkout.

## Local Experiment Evidence

Basic Memory was used as a local proof of concept for `/Users/gengrf/Downloads`.
It is not the final v0.1 implementation.

Observed local state:

```text
Basic Memory repo: /Users/gengrf/basic-memory
Basic Memory database: /Users/gengrf/.basic-memory/memory.db
project: downloads
project path: /Users/gengrf/Downloads
version observed: 0.21.6
entities: 1079
search_index: 1093
relations: 0
semantic search: disabled
```

Conclusion:

```text
Basic Memory is useful for local Markdown memory and MCP-style access.
It does not solve full PDF/DOCX/XLSX/PPTX/OCR/audio/video extraction.
```

## v0.1 Delivery Contract

v0.1 must produce these files:

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

Required behavior:

```text
1. Never modify files under the scanned scope.
2. Index archives/packages by metadata only; do not expand them.
3. Attempt Markdown conversion for PDF, DOCX, XLSX, XLS, and PPTX.
4. Record extraction failures in manifests/failures.jsonl.
5. Re-run incrementally and skip unchanged files.
6. Always generate a Codex-readable context.md.
7. context.md must include paths, summaries, snippets, limitations, and next actions.
8. Cloud validation may use fixtures; real /Users/gengrf/Downloads must be run locally.
```

## Expected Validation Flow

After a real implementation PR exists, validate it locally:

```bash
cd /Users/gengrf/agent-context-system
gh pr checkout <PR_NUMBER>
uv sync
uv run pytest -q
uv run ./agent-context build \
  --scope fixtures/downloads_sample \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
uv run agent-context build \
  --scope fixtures/downloads_sample \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

Then inspect:

```text
manifests/documents.jsonl
manifests/chunks.jsonl
manifests/failures.jsonl
extracted/*.md
reports/downloads_ingestion_report.md
packs/*/context.md
packs/*/sources.jsonl
packs/*/manifest.json
```

Only after fixture validation passes should the real Downloads run happen:

```bash
cd /Users/gengrf/agent-context-system
./agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

## Git Hygiene

Generated private/local data should not be committed by default:

```text
extracted/
manifests/
packs/
reports/*
```

Exceptions:

```text
reports/.gitkeep
reports/*.md
```

This keeps real local file metadata and context packs out of GitHub unless the
user explicitly requests a sanitized export.

## Next Operator Checklist

1. Verify whether the Cloud implementation PR now exists.
2. If no implementation PR exists, open the Cloud task and click `Create PR`.
3. Re-run `gh pr list` and record the PR number.
4. Check out the implementation PR.
5. Run fixture tests and both fixture build commands.
6. Inspect generated manifests, report, and context pack.
7. Confirm archives are metadata-only and source files are unchanged.
8. Only then run against `/Users/gengrf/Downloads` locally.
9. Read `packs/<task-id>/context.md` as Codex input and continue the personal
   assistant memory analysis from that pack.

## Current Risk

The main risk is confusing three separate states:

```text
1. design docs already committed to main
2. Cloud internal diff visible only inside ChatGPT/Codex Cloud
3. real GitHub PR/branch/commit that can be checked out and tested
```

For this project, state 3 is the only acceptable source for implementation
validation.
