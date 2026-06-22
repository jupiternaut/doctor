# Doctor

Doctor is a local-first context runtime for personal agents.

It turns a personal macOS file system, project folders, agent sessions,
workflow documents, indexes, and approved local execution into an agent-usable
runtime. The low-level CLI is still named `agent-context`; `doctor` is the
product name and public abstraction.

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

- [Doctor Handoff](docs/DOCTOR_HANDOFF.md)
- [Doctor Vision Roadmap](docs/DOCTOR_VISION_ROADMAP.md)
- [Doctor Research Scorecard](docs/DOCTOR_RESEARCH_SCORECARD.md)
- [Doctor Douyin v0.1](docs/DOCTOR_DOUYIN_V0_1.md)
- [Doctor Lab](docs/DOCTOR_LAB.md)
- [Context MoE Interface](docs/CONTEXT_MOE_INTERFACE.md)
- [Doctor Runtime OpenSpec](openspec/changes/doctor-runtime-macos-context/proposal.md)
- [Architecture Context](docs/ARCHITECTURE_CONTEXT.md)
- [Architecture v0.3 Vector Diagram](docs/ARCHITECTURE_V0_3_VECTOR.svg)
- [Agent Context Runtime v1.0 Goal](docs/AGENT_CONTEXT_RUNTIME_V1_GOAL.md)
- [Cloud Task: Downloads Context Pack v0.1](docs/CLOUD_TASK_DOWNLOADS_CONTEXT_PACK_V0_1.md)
- [Agent Context System Handoff](docs/HANDOFF.md)
- [File Ingestion Workflow](docs/FILE_INGESTION_WORKFLOW.md)
- [Cold Index And RAG](docs/COLD_INDEX_RAG.md)
- [Context Router Mainstream Framework](docs/CONTEXT_ROUTER_FRAMEWORK.md)
- [Goal: Context Resolver With Subagent Orchestration](docs/GOAL_CONTEXT_RESOLVER_SUBAGENTS.md)
- [MCP Server](docs/MCP_SERVER.md)
- [A/B Context Routes](docs/AB_ROUTES.md)
- [Arena Evaluation](docs/ARENA.md)
- [GitHub Reuse Report](reports/github_reuse_report.md)

## Current Status

This repository currently contains the technical design, local experiment
results, a cloud-executable implementation task, and the local CLI.

The CLI supports v0.1 document ingestion and hot context packs, a v0.2 local
cold index/RAG slice using SQLite, FTS5, and pluggable deterministic
hash-vector-lite retrieval, plus a v0.5 context resolver. The resolver can route
across Downloads, workflow provider cards, project provider cards, project code
indexes, optional `codebase-memory-mcp` graph/text search, Codex/Claude session
provider cards, session transcript indexes, and precomputed `semantic.sqlite`
chunks. MCP
exposes resolver/search/read/build/index/feedback tools. Feedback JSONL now
compiles into `feedback/model.json` and feeds a deterministic rerank prior.
Arena choices are stored as winner/loser pairwise events, and retrieval eval
reports feed a route selector prior, so selected sources
can be boosted and rejected alternatives can be downweighted in later resolver
runs.
`runtime-health` writes a machine-readable v1 acceptance matrix over the current
manifests, indexes, semantic maintenance, feedback loop, safety policy, MCP
surface, and Codex++ integration files so progress is not inferred from a single
green smoke test.
`v1-acceptance` folds the latest runtime health, semantic readiness, MCP live
smoke, and reproducibility snapshot into one handoff report under
`reports/v1-acceptance-latest.md`.

OCR, audio/video transcription, ANN vector search, generated dependency-folder
indexing, and trained rerankers are still not implemented. Dense embeddings are
available through budgeted background refresh and exact local vector scan; the
semantic backend boundary is in place so ANN can be added without changing the
hot pack contract. `config/access_policy.json` now gates provider/path access
for resolver and MCP reads, so sensitive local paths can be denied without
changing ingestion or pack formats.

## Setup

```bash
uv sync
```

## Doctor Douyin v0.1

```bash
doctor-douyin init \
  --out /Users/gengrf/doctor-douyin-data

doctor-douyin sync \
  --source /Users/gengrf/doctor-douyin-data/urls.txt \
  --out /Users/gengrf/doctor-douyin-data
```

The provider writes one Markdown KV file per Douyin URL under
`extracted/douyin/`, plus `manifests/douyin_videos.jsonl`,
`indexes/douyin.sqlite`, `profiles/douyin_user_profile.md`, and
`reports/douyin_ingestion_report.md`.

## Doctor Lab

```bash
uv run ./agent-context lab \
  --out /Users/gengrf/agent-context-system
```

To open a dedicated macOS Terminal window:

```bash
open /Users/gengrf/agent-context-system/scripts/doctor-lab.command
```

The Lab accepts task text plus `/image <path>` attachments, generates a resolver
context pack, shows top sources, and records `/good <n>` or `/bad <n>` feedback
into the existing feedback model.

## Four-Stage Runtime

Stage 1 is a no-index clarification pass. It normalizes the user's natural
language task into a reviewable prompt before Doctor is allowed to read local
indexes or provider manifests:

```bash
agent-context clarify \
  --out /Users/gengrf/agent-context-system \
  --goal "我想比较我的 Codex 项目和一份 AI 应用实习生简历"
```

It writes:

```text
runtime/sessions/<session-id>/clarify.json
runtime/sessions/<session-id>/refined_prompt.md
```

`clarify` records `doctor_access=false`, `resolver_called=false`, and
`index_access=false`. After the user accepts `refined_prompt.md`, pass that
prompt to `codex-preflight`; that second stage generates the reviewable
`model_input.md` context payload.

Stage 2 turns the accepted prompt into a reviewable Doctor context payload:

```bash
agent-context context-review \
  --out /Users/gengrf/agent-context-system \
  --refined-prompt /Users/gengrf/agent-context-system/runtime/sessions/<session-id>/refined_prompt.md \
  --action generate \
  --source-scope all \
  --limit 8
```

It writes:

```text
runtime/sessions/<session-id>/context_review.json
runtime/sessions/<session-id>/context_review.md
runtime/sessions/<session-id>/context_review_events.jsonl
packs/<task-id>-resolve-<timestamp>/model_input.md
```

Approve or reject the generated model input before any answer stage consumes it:

```bash
agent-context context-review \
  --out /Users/gengrf/agent-context-system \
  --session-id <session-id> \
  --action approve \
  --reason "context matches intent"

agent-context context-review \
  --out /Users/gengrf/agent-context-system \
  --session-id <session-id> \
  --action reject \
  --reason "wrong sources"
```

Approve/reject events append to `feedback/context_review_feedback.jsonl`.
`regenerate` reruns `codex-preflight` from the same `refined_prompt.md` after
changing scope, mode, or limit.

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
uv run ./agent-context runtime-health \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3
uv run ./agent-context reproducibility-snapshot \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3
uv run ./agent-context mcp-live-smoke \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --with-manager-feedback-smoke
uv run ./agent-context v1-acceptance \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3
uv run ./agent-context v1-acceptance \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --refresh-evidence
uv run ./agent-context v1-acceptance \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --refresh-evidence \
  --with-manager-feedback-smoke
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

## v0.5 Context Resolver

```bash
agent-context discover-projects \
  --scope /Users/gengrf

agent-context index-projects \
  --project-root /Users/gengrf \
  --max-files-per-project 300

agent-context providers \
  --project-root /Users/gengrf \
  --workflow-root /Users/gengrf/agent-context-system \
  --max-projects 300 \
  --max-sessions 300 \
  --max-workflows 300

agent-context index-sessions \
  --max-sessions 300

agent-context resolve \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --source-scope all \
  --limit 12

agent-context resolve-alternative \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --reject-source "/path/from/previous/sources.jsonl" \
  --reason "not the route I wanted" \
  --source-scope gitProjects

agent-context codex-preflight \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --source-scope gitProjects \
  --mode fast
```

The resolver turns a task goal into a single hot context pack. It uses a
deterministic rule-based plan, multiple cold-index queries, source fusion, and
explainable reranking. Provider cards are metadata summaries: they point Codex
to likely projects, Codex/Claude sessions, and workflow docs. The project code
index then searches selected README/docs/source/config files and returns
concrete file paths. It still skips generated dependency folders and
large/binary files.
`codebase-memory-index` adds an optional external provider path: it turns
Doctor's `extracted/*.md` output into a generated Markdown pseudo repo, then
indexes that repo and any extra `--repo-path` values with unmodified
`codebase-memory-mcp` when the binary is installed.
When `read_source` is called on a Codex or Claude session provider card, it
returns a cleaned transcript preview. Tool calls, tool outputs, environment
blocks, and AGENTS instructions are intentionally omitted so session history is
readable without dumping raw machine context into every hot pack.
When `index-sessions` has been run, resolver can retrieve the relevant cleaned
session chunks directly, so `agentSessions` is no longer limited to matching
only provider-card summaries.

`resolve-alternative` is the "this is wrong" path: it records rejected sources
as negative feedback, refreshes `feedback/model.json`, and generates a new hot
context pack while filtering those sources from the candidate pool.

Optional codebase-memory provider:

```bash
agent-context codebase-memory-index \
  --out /Users/gengrf/agent-context-system \
  --repo-path /Users/gengrf/agent-context-system

agent-context codebase-memory-search \
  --out /Users/gengrf/agent-context-system \
  --query "context resolver recommendation feedback" \
  --limit 8

agent-context resolve \
  --out /Users/gengrf/agent-context-system \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --source-scope codebaseMemory \
  --limit 8
```

The command writes:

```text
manifests/projects.jsonl
manifests/sessions.jsonl
manifests/workflows.jsonl
manifests/session_documents.jsonl
manifests/session_chunks.jsonl
manifests/session_failures.jsonl
manifests/project_documents.jsonl
manifests/project_chunks.jsonl
manifests/symbols.jsonl
manifests/codebase_memory_sources.jsonl
indexes/projects.sqlite
indexes/sessions.sqlite
providers/codebase_memory/markitdown_extracted_repo/
packs/<task-id>-resolve-<timestamp>/context.md
packs/<task-id>-resolve-<timestamp>/sources.jsonl
packs/<task-id>-resolve-<timestamp>/manifest.json
packs/<task-id>-resolve-<timestamp>/resolution_plan.json
packs/<task-id>-resolve-<timestamp>/codex_preflight.md
packs/<task-id>-resolve-<timestamp>/model_input.md
```

`codex-preflight` is the decoupled entry point for Codex++ or another wrapper:
it calls the resolver when `auto_context` is enabled, writes
`codex_preflight.md` and a reviewable `model_input.md`, then returns the
context/sources/manifest/model-input paths. `model_input.md` is the visible
Doctor context payload proposed for the model; it does not include hidden
platform or client system prompts.

`panel` writes a UI-friendly status contract and a local HTML panel:

```bash
agent-context panel \
  --out /Users/gengrf/agent-context-system \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --source-scope gitProjects \
  --mode fast

agent-context panel-feedback \
  --out /Users/gengrf/agent-context-system \
  --source "/path/or/source-id" \
  --rating useful \
  --reason "matched my task"
```

The command writes:

```text
panel/status.json
panel/context_panel.html
feedback/panel_feedback.jsonl
```

`panel/status.json` includes an `access_audit` block with recent
metadata-only read/filter events from `reports/access_audit.jsonl` and a
`feedback.replay_trend` block with replay health, latest expected top1 rate,
and expected-rank regression counts. The HTML panel renders those health fields
plus `semantic_readiness` from the latest readiness report and a short Access
Audit table so a wrapper UI can show what was read or blocked without exposing
source text.

## MCP Server

```bash
uv run agent-context mcp --out /Users/gengrf/agent-context-system
```

The MCP server exposes local tools for `resolve_context`, `search_context`,
`index_context`, `refresh_providers`, `index_projects`,
`codebase_memory_index`, `codebase_memory_search`, `index_sessions`, `build_hot_pack`, `read_source`,
`context_panel`, `record_feedback`, `record_panel_feedback`,
`resolve_alternative_context`,
`semantic_refresh`, `semantic_maintain`, `semantic_ann_prune`,
`semantic_launchd_status`, `semantic_launchd_monitor`,
`semantic_launchd_audit`, `semantic_launchd_trend`, `semantic_readiness`,
`semantic_benchmark`,
`retrieval_eval`, `retrieval_eval_cases`, `feedback_replay`,
`feedback_replay_cases`, `feedback_replay_trend`, `route_selector_model`,
`runtime_health`, `v1_acceptance`, `codex_plus_smoke`,
`reproducibility_snapshot`, and
`semantic_index_status`. It uses the existing
SQLite cold index and generated context packs rather than introducing a
separate storage layer.

Use `mcp-live-smoke` when you need proof that an MCP client, not only in-process
Python functions, can initialize the stdio server, list tools, call
`runtime_health`, call `v1_acceptance`, and read a generated report through
`read_source`. Add `--with-manager-feedback-smoke` for final/release evidence so
the MCP smoke preserves the stronger Codex++ Manager feedback-replay contract
requirement when it calls v1 tools.

Use `codex-plus-smoke` for a Codex++ integration check. By default it
runs `scripts/smoke-agent-context-panel-status.mjs`, proving Codex++ core and
Manager consume `panel/status.json` without launching Codex.app. Pass
`--with-manager-feedback` when release evidence should include the Manager
feedback replay source/status contract evidence. Pass `--with-runtime` only
when you explicitly want the GUI runtime smoke.

## Access Policy

```bash
agent-context access-policy --out /Users/gengrf/agent-context-system
agent-context access-policy --out /Users/gengrf/agent-context-system --write-default
agent-context access-policy \
  --out /Users/gengrf/agent-context-system \
  --deny-path "*/Secrets/*" \
  --deny-provider claude_session \
  --require-consent-provider codex_session \
  --require-consent-path "*/PrivateNotes/*" \
  --audit-max-bytes 5000000 \
  --audit-max-rotated-files 3
agent-context access-consent \
  --out /Users/gengrf/agent-context-system \
  --identifier "project:example" \
  --reason "approved for this task"
agent-context access-audit --out /Users/gengrf/agent-context-system --limit 50
```

The policy lives at:

```text
config/access_policy.json
```

It supports `allow_providers`, `deny_providers`, `deny_path_patterns`,
`require_consent_providers`, `require_consent_path_patterns`,
`audit_max_bytes`, and `audit_max_rotated_files`.
Resolver filters provider/index candidates through this policy before writing
`sources.jsonl`; MCP `read_source` also enforces the same policy before
returning indexed chunks, provider cards, session previews, or generated
artifacts. Raw path reads outside `--out` are still rejected even when no deny
pattern matches. Consent rules do not remove candidates from resolver output;
they only block `read_source` until `agent-context access-consent` or MCP
`grant_access_consent` grants that exact source. Resolver filtering and MCP
reads append local audit events to:

```text
reports/access_audit.jsonl
```

The audit log records metadata only: action, decision, identifier, provider,
path, reason, and summary counts. It does not store source text. When the active
audit file exceeds `audit_max_bytes`, it is compressed to
`reports/access_audit.jsonl.1.gz` and older files are shifted up to
`audit_max_rotated_files`; `access-audit` reads both active and rotated logs.

## Feedback And Semantic Backends

```bash
agent-context feedback-model --out /Users/gengrf/agent-context-system
agent-context feedback-replay-cases --out /Users/gengrf/agent-context-system
agent-context feedback-replay \
  --out /Users/gengrf/agent-context-system \
  --case "告诉我本地所有项目里如何构建个人推荐系统" \
  --source-scope gitProjects
agent-context feedback-replay-trend --out /Users/gengrf/agent-context-system --max-reports 20
agent-context retrieval-eval \
  --out /Users/gengrf/agent-context-system \
  --case "recommendation system ranking feedback local project => data/preference_state.json" \
  --source projects \
  --limit 8
agent-context retrieval-eval-cases --out /Users/gengrf/agent-context-system
agent-context route-selector-model --out /Users/gengrf/agent-context-system
agent-context runtime-health \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3
agent-context semantic-readiness --out /Users/gengrf/agent-context-system --with-launchctl
agent-context reproducibility-snapshot \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3
agent-context mcp-live-smoke \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --with-manager-feedback-smoke
agent-context v1-acceptance \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3
agent-context v1-acceptance \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --refresh-evidence
agent-context v1-acceptance \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --refresh-evidence \
  --with-manager-feedback-smoke
agent-context v1-refresh \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --with-manager-feedback-smoke
agent-context v1-refresh \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --wait-for-semantic-evidence \
  --semantic-wait-timeout-seconds 7200 \
  --semantic-wait-poll-seconds 60 \
  --with-manager-feedback-smoke
agent-context semantic-status
agent-context semantic-refresh --out /Users/gengrf/agent-context-system --source all --budget 32
agent-context semantic-refresh --out /Users/gengrf/agent-context-system --source sessions --budget 32
agent-context semantic-maintain --out /Users/gengrf/agent-context-system --source all --budget 32 --max-jobs 2 --min-interval-minutes 30
agent-context semantic-index-status --out /Users/gengrf/agent-context-system
agent-context semantic-benchmark --out /Users/gengrf/agent-context-system --source projects --query "告诉我本地所有项目里如何构建个人推荐系统" --limit 8
agent-context semantic-ann-prune --out /Users/gengrf/agent-context-system --max-entries 32 --max-bytes 1000000000
agent-context semantic-launchd --out /Users/gengrf/agent-context-system --print
agent-context semantic-launchd-status --out /Users/gengrf/agent-context-system --with-launchctl
agent-context semantic-launchd-monitor --out /Users/gengrf/agent-context-system --with-launchctl
agent-context semantic-launchd-wait --out /Users/gengrf/agent-context-system --timeout-seconds 7200 --poll-seconds 60
agent-context semantic-launchd-audit --out /Users/gengrf/agent-context-system --max-history 200
agent-context semantic-launchd-audit --out /Users/gengrf/agent-context-system --notify --notify-on alert
agent-context semantic-launchd-recover --out /Users/gengrf/agent-context-system
agent-context semantic-launchd-recover --out /Users/gengrf/agent-context-system --apply
agent-context semantic-launchd-recover --out /Users/gengrf/agent-context-system --apply --verify-after-apply
agent-context semantic-launchd-trend --out /Users/gengrf/agent-context-system --min-days 2
```

`feedback-model` compiles arena, MCP, panel, alternative, and replay supervision
feedback into
`feedback/model.json`. Source-level feedback expands to related project keys
such as `project_id`, `project_path`, `project_name`, and source group, so later
resolver runs can adjust ranking beyond one exact path. Arena feedback also
records pairwise winner/loser comparisons and applies positive prior to winner
sources/routes and negative prior to loser sources/routes. It also computes a
small Elo-style pairwise prior from arena choices. Replay cases with
`expected_source` add a bounded same-family prior for the directly labeled
source without automatically boosting sibling files in the same project.
Feedback can be scoped by query family, so a choice made for a
recommendation-system task does not have to carry the same weight for unrelated
session-history or document research tasks. `feedback-replay-cases` turns
existing arena/eval/MCP/panel/alternative
feedback into `feedback/replay_cases.generated.jsonl`; `feedback-replay`
evaluates manual plus generated fixed goals with and without the current
feedback model and writes before/after reports under `reports/`. The
command-line `--limit` caps each case budget, even when a generated case stores
its own larger limit. `feedback-replay-trend` reads existing
`reports/feedback_replay_*.json` files and writes a health report that flags
expected-source loss, expected-rank regressions, and insufficient replay
history without mutating the feedback model.

`reproducibility-snapshot` records a release checkpoint for dirty worktrees
when changes are not committed yet. It writes branch, HEAD, `git status
--short`, a status hash, diff stats, and small changed-file hashes to
`reports/reproducibility-snapshot-latest.json` and `.md`. `runtime-health`
accepts dirty worktrees only when the latest snapshot still matches the current
branch, HEAD, and status hash for every inspected root; stale snapshots fall
back to warning.

`semantic-status` reports the active retrieval backend and optional local
embedding/ANN packages if installed. The default foreground path remains local
`hash-vector-lite`; dense embeddings are handled through budgeted background
refresh jobs and are fused by the resolver when `indexes/semantic.sqlite`
contains matching chunks.
Semantic refresh sources are `downloads`, `projects`, `sessions`, and `all`;
`all` includes all three indexed families when their SQLite indexes exist.

To enable the local FastEmbed backend for semantic refresh jobs:

```bash
uv sync --extra embeddings
agent-context semantic-refresh --source all --budget 32
agent-context semantic-maintain --source all --budget 32 --max-jobs 2
```

Optional model override:

```bash
AGENT_CONTEXT_FASTEMBED_MODEL=BAAI/bge-small-en-v1.5
```

FastEmbed defaults to exact local vector scoring over SQLite rows. To enable
optional ANN retrieval for resolver semantic fusion, install `hnswlib` and set:

```bash
AGENT_CONTEXT_ANN_BACKEND=hnswlib
```

When `hnswlib` is unavailable or the stored embeddings are not dense vectors,
the resolver falls back to exact local vector scoring and records the fallback
reason in `resolution_plan.json`. HNSW indexes are cached under
`indexes/semantic_ann/` with a row fingerprint, so repeated resolver queries can
load the same ANN graph until the semantic rows change. Full foreground dense
rebuilds are intentionally avoided for large local scopes; use
`semantic-maintain` from a launchd/cron wrapper for repeatable background
maintenance. It runs one or more budgeted refresh jobs, skips when the latest
job is inside `--min-interval-minutes`, and writes JSON/Markdown reports under
`reports/`. `semantic-ann-prune` removes stale HNSW cache files whose
fingerprint no longer matches current semantic rows, and can also enforce
entry/byte limits; use `--dry-run` before deleting. `semantic-launchd --print`
renders the macOS LaunchAgent plist and maintenance script; `--install` writes
them to `~/Library/LaunchAgents`, `out/scripts/`, and `out/logs/`, but it does
not automatically call `launchctl bootstrap`. Register the installed LaunchAgent
explicitly with:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gengrf.agent-context.semantic-maintenance.plist
```

Force one launchd-managed run for smoke testing:

```bash
launchctl kickstart -k gui/$(id -u)/com.gengrf.agent-context.semantic-maintenance
agent-context semantic-launchd-status --out /Users/gengrf/agent-context-system --with-launchctl
agent-context semantic-launchd-monitor --out /Users/gengrf/agent-context-system --with-launchctl
```

Rollback:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.gengrf.agent-context.semantic-maintenance.plist
agent-context semantic-launchd --out /Users/gengrf/agent-context-system --uninstall
```

`semantic-launchd-status` is a read-only health check: it reports whether the
plist/script/log directory match, whether the script still runs both
`semantic-maintain` and `semantic-ann-prune`, plus recent maintenance/prune
reports and log tails. Add `--with-launchctl` to also run read-only
`launchctl print` and report whether the LaunchAgent is loaded, including
structured `state`, `runs`, `last_exit_code`, and `run_interval_seconds` fields.
`semantic-launchd-monitor` appends this status as
`reports/semantic-launchd-monitor.jsonl` and writes latest JSON/Markdown
summaries. The generated LaunchAgent script runs it after maintenance and ANN
prune, so every background run leaves a health snapshot. The latest summary also
includes `latest_snapshot_age_seconds`, `latest_launchd_activity_at`,
`next_expected_run_after`, `seconds_until_next_expected_run`,
`natural_run_due`, `natural_run_overdue`, and `seconds_overdue`, which make the
next natural launchd cycle auditable without guessing. Use
`semantic-launchd-wait` for a one-shot acceptance check that polls the same
read-only monitor until launchd `runs` increases or the latest background
activity timestamp advances; it writes `reports/semantic-launchd-wait-*.json`
and `.md` whether it succeeds or times out. `semantic-launchd-audit` does not
append a new monitor snapshot; it reads existing
`reports/semantic-launchd-monitor.jsonl`, writes
`reports/semantic-launchd-audit-*.json/.md` plus latest audit files, and reports
`ok`, `warning`, or `alert` with explicit alert codes for stale snapshots,
overdue natural runs, non-zero launchd exits, failed maintain/prune reports,
stderr output, and consecutive unhealthy snapshots. Add `--notify` to send a
macOS notification through `osascript`; by default notifications are disabled.
`--notify-on alert` only notifies for alert health, while `--notify-on warning`
also notifies for warning health. `semantic-launchd-recover` turns status and
audit results into a recovery plan. It defaults to dry-run and writes
`reports/semantic-launchd-recover-*.json/.md`; pass `--apply` explicitly to
install missing plist/script files, bootstrap an unloaded LaunchAgent, kickstart
a loaded but failed/overdue LaunchAgent, or collect a fresh monitor snapshot for
history-only warnings. Add `--verify-after-apply` to run status, monitor, and
audit after applying recovery actions and record pass/fail checks in the
recovery report. `semantic-launchd-trend` summarizes existing monitor
history into day/hour buckets and marks current evidence as `short_window` until
the configured number of days has been observed. `runtime-health` keeps that as
a warning, but now also exposes `semantic_background.evidence.readiness` with
the current semantic chunk count, monitor health, observed trend days, days
remaining, next monitor due time, and earliest new-day check time. This makes a
healthy single-day background index distinguishable from a broken one.
`semantic-readiness` writes the same focused readiness judgement to
`reports/semantic-readiness-*.json/.md` for wrappers or MCP clients that only
need to know whether semantic background retrieval is ready, waiting for time,
or needs attention.
Resolver semantic fusion is guarded by `AGENT_CONTEXT_MIN_SEMANTIC_ROWS`
(default `16`) so a tiny warm-up index cannot dominate useful FTS/project-code
results. Semantic-only hits are also downweighted unless their path/snippet has
enough lexical support for the query; this keeps dense recall as a side channel
instead of letting one noisy project dominate the context pack. Background
refresh sampling is diversified by `AGENT_CONTEXT_SEMANTIC_REFRESH_BUCKET_CAP`
(default `4`) so early refresh jobs do not fill the semantic index from one
project or path bucket.

To compare retrieval modes without rewriting the main index:

```bash
agent-context semantic-benchmark \
  --out /Users/gengrf/agent-context-system \
  --source projects \
  --query "告诉我本地所有项目里如何构建个人推荐系统" \
  --query "recommendation system ranking feedback local project" \
  --limit 8
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
candidate. Recording feedback appends `feedback/arena_feedback.jsonl` and
refreshes `feedback/model.json`. It also writes `feedback/retrieval_eval_cases.jsonl`,
so later `retrieval-eval` runs and the resolver route selector can learn which
retrieval path matches the user's chosen evidence:

```bash
agent-context feedback \
  --slate packs/<task-id>-arena-<timestamp>/slate.json \
  --winner candidate-2 \
  --reason "best matches my intent"
```

For long-running use, keep the raw feedback append-only and curate it before
evaluation:

```bash
agent-context retrieval-eval-cases --out /Users/gengrf/agent-context-system
agent-context retrieval-eval --out /Users/gengrf/agent-context-system --source downloads
```

`retrieval-eval` prefers a non-empty
`feedback/retrieval_eval_cases.curated.jsonl`, then falls back to the raw
`feedback/retrieval_eval_cases.jsonl`.
If no user-labeled arena cases exist yet, bootstrap labeled runtime self-test
cases from already-indexed project files without editing the raw feedback log:

```bash
agent-context retrieval-eval-cases \
  --out /Users/gengrf/agent-context-system \
  --bootstrap-runtime
```

Bootstrap cases are marked `origin=runtime_bootstrap`; they verify retrieval and
route-selection mechanics, but they are not treated as user preference feedback.
`retrieval-eval` also refreshes `feedback/route_selector_model.json`; use
`route-selector-model` when you need to rebuild that model from existing
`reports/retrieval_eval_*.json` files.
