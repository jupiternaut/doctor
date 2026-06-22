# MCP Server

`agent-context mcp` exposes the local context system through the Model Context
Protocol over stdio.

The MCP layer is intentionally thin. It does not replace the cold index, RAG
query packs, or hot context packs. It lets MCP clients call those existing
contracts.

For live acceptance evidence, run `agent-context mcp-live-smoke`. It starts the
stdio MCP server, connects with the official MCP client, lists tools, calls
`runtime_health`, calls `v1_acceptance`, and reads the generated health report
back through `read_source`. For final/release evidence, pass
`--with-manager-feedback-smoke`; the smoke preserves that stronger requirement
when it calls `v1_acceptance` and `v1_followup`. The smoke report records both
the background evidence gate (`v1_*_next_evidence_gate_*`) and final acceptance
gate (`v1_*_acceptance_gate_*`) so MCP clients do not need to infer which clock
is blocking v1.

## Tools

```text
resolve_context(goal, limit=12, source_scope="all")
  Resolve a task goal into a plan, multiple retrieval queries, fused sources,
  and a Codex-readable hot context pack.

resolve_alternative_context(goal, rejected_sources, reason="", limit=12, source_scope="all")
  Record rejected sources as negative feedback and resolve a replacement context pack.

doctor_run(goal, session_id=null, mode="standard")
  Start a Doctor runtime session with no-index clarification. This creates
  runtime/sessions/<session-id>/DOCTOR_SESSION.md and does not call the resolver.

doctor_agent_preflight(advance="clarify", goal=null, session_id=null, source_scope="all", limit=8, mode="fast", agent_command="<agent command>", review_port=8765)
  Default Doctor runtime preflight entrypoint for Codex++, Warp, Codex CLI, and MCP clients. Use advance=clarify for the first no-index user prompt review, advance=context after the refined prompt is accepted, and advance=handoff after model_input.md is approved.

doctor_session(session_id)
  Inspect a Doctor runtime session and refresh DOCTOR_SESSION.md.

doctor_runtime_acceptance(session_id)
  Write reports/runtime-vm-acceptance-*.json/.md for one runtime session.

doctor_runtime_handoff(session_id)
  Export the approved model_input.md as an agent_handoff.md/json packet for Codex++, Warp, or Doctor.

doctor_runtime_adapter(session_id, targets=null, agent_command="<agent command>", review_port=8765)
  Export adapter files for Codex++, Warp, Codex CLI, and MCP clients. The package includes adapter_manifest.json, target-specific Markdown, shell environment helpers, and an MCP tool sequence.

doctor_runtime_review_client(session_id, review_server_url="http://127.0.0.1:8765/")
  Export an embeddable HTML/JS review client plus runtime-review-api-contract.json for Codex++, Warp, Codex CLI, or MCP clients.

doctor_context_review(action="generate", session_id=null, refined_prompt_path=null, reason="", source_scope="all", limit=12, mode="fast")
  Generate, regenerate, approve, or reject the reviewable model_input.md payload.

doctor_answer_review(action="prepare", session_id="", answer_text="", answer_file=null, command="", cwd=null, timeout_seconds=120, reason="")
  Prepare, run, record, approve, or reject the answer packet after context approval and runtime handoff export. The run action passes answer_packet.md on stdin and captures stdout as the candidate answer.

doctor_execution_review(action="prepare", session_id="", command="", cwd=null, timeout_seconds=120, artifact_file=null, reason="")
  Prepare, run, record, approve, or reject local execution artifacts after answer approval.

search_context(query, limit=12)
  Query the cold index and write a RAG context pack.

index_context()
  Rebuild indexes/context.sqlite from manifests/*.jsonl.

refresh_providers(project_roots=null, sessions_root=null, claude_root=null, workflow_roots=null, max_projects=300, max_sessions=300, max_workflows=300)
  Refresh project discovery, Codex/Claude session cards, and workflow doc cards.

index_projects(project_roots=null, max_projects=300, max_files_per_project=300)
  Index project README/docs/source files into indexes/projects.sqlite.

index_sessions(max_sessions=300, max_messages_per_session=1000)
  Index Codex/Claude cleaned transcript previews into indexes/sessions.sqlite.

build_hot_pack(scope, goal, with_index=false)
  Run ingestion and write a Codex-readable hot context pack.

read_source(identifier, max_chars=4000)
  Read a source by path, source_id, source_chunk_id, chunk_id, doc_id, or relative path.
  Session provider cards are rendered as cleaned transcript previews; full tool
  calls, tool outputs, environment blocks, and AGENTS instructions are omitted.
  Reads are filtered by config/access_policy.json.

record_feedback(query_id, selected_source, reason="", rating=null)
  Append user feedback to feedback/mcp_feedback.jsonl.

context_panel(goal=null, source_scope="all", mode="fast", limit=12, auto_context=true)
  Write panel/status.json and panel/context_panel.html, optionally running resolver first. Status includes feedback.replay_trend health and access audit metadata.

record_panel_feedback(source, rating, reason="", status_path=null)
  Append panel feedback to feedback/panel_feedback.jsonl and compile feedback/model.json.

feedback_replay(cases_path=null, case_goals=[], source_scope="all", limit=12)
  Replay fixed goals before/after feedback rerank and write reports/feedback_replay_*.json and .md. Defaults to manual plus generated replay cases; limit caps per-case source budget.

feedback_replay_cases(output_cases_path=null, source_scope="all", limit=12)
  Generate feedback/replay_cases.generated.jsonl from arena, retrieval eval, MCP, panel, and alternative feedback logs without editing raw feedback.

feedback_replay_trend(max_reports=20, min_reports=2)
  Summarize reports/feedback_replay_*.json history and write a health report for expected-rank regressions or missing expected sources.

retrieval_eval(cases_path=null, inline_cases=[], source="projects", limit=8)
  Evaluate hash-vector-lite, FastEmbed rerank, and semantic-fusion against labeled expected sources. Use inline cases like "query => expected path" or a JSONL case file with query/source/expected_sources. Writes reports/retrieval_eval_*.json and .md.

retrieval_eval_cases(cases_path=null, output_cases_path=null, max_age_days=0, source="projects", bootstrap_runtime=false)
  Curate raw feedback/retrieval_eval_cases.jsonl into deduped feedback/retrieval_eval_cases.curated.jsonl without editing the raw log. bootstrap_runtime=true adds labeled runtime self-test cases from already-indexed project files, marked origin=runtime_bootstrap. Writes reports/retrieval_eval_cases_*.json and .md.

route_selector_model(max_reports=50)
  Compile retrieval eval reports into feedback/route_selector_model.json. Resolver reads this persisted model when it is fresh.

runtime_health(codex_plus_root=null, min_documents=1, min_projects=1, min_sessions=1, min_workflows=1, min_semantic_chunks=16)
  Write reports/runtime-health-*.json/.md plus latest copies. The report includes a v1 acceptance matrix across provider coverage, cold indexes, semantic background maintenance, hot context packs, feedback sample coverage, safety policy, MCP tool surface, Codex++ integration files, and dirty-worktree reproducibility.

v1_acceptance(codex_plus_root=null, refresh_health=false, refresh_evidence=false, min_documents=1, min_projects=1, min_sessions=1, min_workflows=1, min_semantic_chunks=16, required_trend_days=2, mcp_timeout_seconds=60, codex_plus_timeout_seconds=120, with_manager_feedback_smoke=false)
  Write reports/v1-acceptance-*.json/.md plus latest copies and reports/v1-followup-*.json/.md plus latest copies. It folds latest runtime_health, semantic_readiness, MCP live smoke, Codex++ smoke, and reproducibility snapshot evidence into one handoff report, and returns waiting_for_time when the only remaining blocker is multi-day semantic trend evidence. The follow-up plan records earliest_recheck_after, next_monitor_due_at, can_recheck_now, report paths, and exact commands for the next acceptance pass. Set refresh_evidence=true to run the full recheck sequence: semantic launchd monitor, semantic launchd audit, semantic launchd trend, semantic readiness, reproducibility snapshot, Codex++ smoke, MCP live smoke, runtime health, then the acceptance report. Set with_manager_feedback_smoke=true when final/release evidence should also prove the Codex++ Manager render smoke and screenshot path.

v1_followup(codex_plus_root=null, run_when_ready=false, force=false, min_documents=1, min_projects=1, min_sessions=1, min_workflows=1, min_semantic_chunks=16, required_trend_days=2, mcp_timeout_seconds=60, codex_plus_timeout_seconds=120, with_manager_feedback_smoke=false)
  Read reports/v1-followup-latest.json, recompute can_recheck_now against the current clock, and write reports/v1-followup-check-*.json/.md plus latest copies. The check report includes wait_reason, next_gate_at, and seconds_until_next_gate for the next background evidence gate, plus acceptance_wait_reason, acceptance_gate_at, and seconds_until_acceptance_gate for the final v1 acceptance gate. With run_when_ready=true it only runs v1_acceptance(refresh_evidence=true) when the follow-up gate is due. With force=true it runs the refresh immediately. Set with_manager_feedback_smoke=true to pass the same stronger Codex++ Manager render-smoke requirement through to the gated refresh.

v1_refresh(codex_plus_root=null, force=false, refresh_semantic_evidence=true, refresh_mcp_smoke=true, refresh_runtime_health=true, min_documents=1, min_projects=1, min_sessions=1, min_workflows=1, min_semantic_chunks=16, required_trend_days=2, mcp_timeout_seconds=60, codex_plus_timeout_seconds=120, wait_for_semantic_evidence=false, semantic_wait_timeout_seconds=7200, semantic_wait_poll_seconds=60, with_manager_feedback_smoke=false)
  Safely run the daily v1 refresh loop for agents and UI: semantic monitor/audit/trend/readiness when the evidence gate is due, MCP live smoke, lightweight runtime_health, v1_followup(run_when_ready=true), v1_stage_status, and Context Panel status/HTML sync. It does not force heavy acceptance work before the gate unless force=true, so agents can call it repeatedly without accidentally bypassing the semantic background time gate. Set wait_for_semantic_evidence=true when an agent should wait for the next natural semantic LaunchAgent run before refreshing semantic evidence. Set refresh_semantic_evidence=false, refresh_mcp_smoke=false, and refresh_runtime_health=false together only for a pure latest-file read/sync pass.
  When semantic evidence is refreshed, semantic_evidence.consumed_gate_at records the gate that was just satisfied, while semantic_evidence.next_gate_at records the next monitor due time from refreshed semantic readiness.

v1_stage_status(codex_plus_root=null)
  Read latest runtime/acceptance/follow-up/semantic/MCP/Codex++ evidence and write reports/v1-stage-status-*.json/.md plus latest copies. This is a lightweight status view: it does not rescan files, rebuild indexes, or refresh semantic jobs. Use it when an agent or UI needs the current phase table, evidence paths, and gates. The next_gates object keeps compatibility fields wait_reason/next_gate_at for the next background evidence gate, and also exposes explicit next_evidence_gate_reason/next_evidence_gate_at/seconds_until_next_evidence_gate plus acceptance_wait_reason/acceptance_gate_at/seconds_until_acceptance_gate for the final v1 acceptance gate.

codex_plus_smoke(codex_plus_root=null, timeout_seconds=120, with_manager_feedback=false, with_runtime=false)
  Run headless Codex++ Agent Context smoke scripts and write reports/codex-plus-smoke-*.json/.md plus latest copies. By default this runs the panel/status contract smoke only; with_runtime=true may launch Codex.app.

semantic_readiness(min_semantic_chunks=16, required_trend_days=2, label="com.gengrf.agent-context.semantic-maintenance", with_launchctl=false)
  Write reports/semantic-readiness-*.json/.md plus latest copies. Returns ready, waiting_for_time, or attention_required for the background semantic index without requiring the caller to parse the full runtime_health matrix.

reproducibility_snapshot(codex_plus_root=null)
  Write reports/reproducibility-snapshot-*.json/.md plus latest copies. It records branch, HEAD, git status hash, diff stats, and small changed-file hashes for the agent-context root and optional Codex++ root. runtime_health treats dirty worktrees as covered only when the latest snapshot still matches current git state.

semantic_refresh(source="all", budget=32, backend="fastembed", text_chars=800)
  Run a budgeted background semantic embedding refresh job.
  Supported sources are downloads, projects, sessions, and all.

semantic_maintain(source="all", budget=32, backend="fastembed", text_chars=800, max_jobs=1, min_interval_minutes=0)
  Run a scheduler-safe semantic maintenance pass and write JSON/Markdown reports.
  Supported sources are downloads, projects, sessions, and all.

semantic_ann_prune(max_entries=32, max_bytes=1000000000, dry_run=false)
  Prune stale or excessive indexes/semantic_ann cache files and write JSON/Markdown reports.

semantic_launchd_status(label="com.gengrf.agent-context.semantic-maintenance", tail_lines=20, with_launchctl=false)
  Read semantic LaunchAgent installation, latest reports, and log-tail status. Set with_launchctl=true for read-only launchctl loaded-state checks including state, runs, last_exit_code, and run_interval_seconds.

semantic_launchd_monitor(label="com.gengrf.agent-context.semantic-maintenance", tail_lines=20, with_launchctl=true, max_history=200)
  Append a semantic LaunchAgent health snapshot to reports/semantic-launchd-monitor.jsonl and write latest JSON/Markdown summaries, including latest_launchd_activity_at, next_expected_run_after, natural_run_due, natural_run_overdue, and seconds_overdue.

semantic_launchd_audit(max_history=200, min_snapshots=2, consecutive_unhealthy_threshold=3, max_snapshot_age_seconds=null, notify=false, notify_on="alert")
  Audit existing semantic LaunchAgent monitor history without appending a new snapshot. Writes reports/semantic-launchd-audit-*.json/.md and returns ok, warning, or alert with explicit alert codes. Set notify=true to send an optional macOS notification when health reaches notify_on.

semantic_launchd_recover(apply=false, verify_after_apply=false, label="com.gengrf.agent-context.semantic-maintenance", max_history=200, agent_context_bin="agent-context")
  Plan or apply recovery actions for semantic LaunchAgent maintenance. Defaults to dry-run; set apply=true only when the MCP client should install, bootstrap, kickstart, or collect monitor snapshots. Set verify_after_apply=true to run status, monitor, and audit after applying actions.

semantic_launchd_trend(max_history=1000, min_days=2)
  Summarize existing semantic LaunchAgent monitor history into day/hour buckets. Returns short_window until enough days are observed for multi-day confidence.

semantic_benchmark(source="projects", queries=[], limit=8)
  Compare hash-vector-lite, FastEmbed rerank, and semantic-fusion retrieval modes. Writes reports/embedding_backend_benchmark_*.md and does not rewrite the main indexes.

semantic_index_status()
  Report indexes/semantic.sqlite progress and the latest semantic job.

access_audit(limit=50)
  Return recent reports/access_audit.jsonl events for resolver filtering and MCP reads.

access_policy(allow_providers=null, remove_allow_providers=null, deny_providers=null, remove_deny_providers=null, deny_path_patterns=null, remove_deny_path_patterns=null, require_consent_providers=null, remove_require_consent_providers=null, require_consent_path_patterns=null, remove_require_consent_path_patterns=null, audit_max_bytes=null, audit_max_rotated_files=null)
  Show or patch config/access_policy.json without hand-editing the file.

grant_access_consent(identifier, reason="")
  Grant read consent for one indexed/provider/generated source after a consent_required event.
```

Feedback is recorded through MCP and compiled through the CLI:

```bash
agent-context feedback-model --out /Users/gengrf/agent-context-system
```

The compiled `feedback/model.json` is read by `resolve_context` and adjusts
source ranking deterministically. The model includes global source/route priors
and query-family scoped priors; `resolution_plan.json` records the active
`query_family`, and each selected source exposes global and scoped feedback
parts in `resolver_score_parts`.

For Codex++ or another wrapper that wants a default preflight before each task,
use the runtime preflight entry point instead of embedding resolver logic in the UI:

```bash
agent-context agent-preflight \
  --out /Users/gengrf/agent-context-system \
  --advance clarify \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --session-id <session-id>
```

For a wrapper UI, `context_panel` is the MCP equivalent of:

```bash
agent-context panel \
  --out /Users/gengrf/agent-context-system \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --source-scope gitProjects \
  --mode fast
```

## Start Manually

```bash
cd /Users/gengrf/agent-context-system
uv run agent-context mcp --out /Users/gengrf/agent-context-system
```

The server uses stdio, so this command is normally launched by an MCP client
rather than run directly in a human terminal.

## Client Config

Use this shape for clients that accept MCP server JSON:

```json
{
  "mcpServers": {
    "agent-context": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/Users/gengrf/agent-context-system",
        "agent-context",
        "mcp",
        "--out",
        "/Users/gengrf/agent-context-system"
      ]
    }
  }
}
```

The `--out` directory must contain the generated project data:

```text
manifests/documents.jsonl
manifests/chunks.jsonl
manifests/failures.jsonl
manifests/projects.jsonl
manifests/sessions.jsonl
manifests/workflows.jsonl
manifests/session_documents.jsonl
manifests/session_chunks.jsonl
manifests/session_failures.jsonl
manifests/project_documents.jsonl
manifests/project_chunks.jsonl
manifests/symbols.jsonl
indexes/context.sqlite
indexes/projects.sqlite
indexes/sessions.sqlite
```

If `indexes/context.sqlite` is missing, `search_context` will try to rebuild it
from the manifests.

## Typical Flow

```text
1. refresh_providers(
     project_roots=["/Users/gengrf"],
     sessions_root="/Users/gengrf/.codex/sessions",
     claude_root="/Users/gengrf/.claude/projects",
     workflow_roots=["/Users/gengrf/agent-context-system"]
   )
2. index_projects(["/Users/gengrf"], 300, 300)
3. index_sessions(300, 1000)
4. resolve_context("告诉我本地所有项目里如何构建个人推荐系统", 12, "all")
5. read_source("<path or source_chunk_id from top_sources>")
6. record_feedback("<task_id or query_id>", "<selected source>", "useful")
7. runtime_health("/Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3")
```

Equivalent live smoke:

```bash
agent-context mcp-live-smoke \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --with-manager-feedback-smoke
```

Supported `source_scope` values are `downloads`, `gitProjects`,
`codexSessions`, `agentSessions`, `workflowDocs`, and `all`.
`agentSessions` reads the same session provider manifest as `codexSessions`,
but the manifest can contain both Codex and Claude session cards. When
`indexes/sessions.sqlite` exists, resolver can return transcript chunks from
that index before falling back to provider cards.

Semantic fusion uses `indexes/semantic.sqlite` when it exists. Exact local
vector scoring is the default. Set `AGENT_CONTEXT_ANN_BACKEND=hnswlib` before
starting the MCP server to enable optional HNSW ANN retrieval; if `hnswlib` is
missing or the stored embeddings are incompatible, resolver falls back to exact
scoring and records the reason in `resolution_plan.json`.
The optional HNSW graph and label metadata are cached under
`indexes/semantic_ann/` and invalidated by a semantic-row fingerprint.
Use `semantic_ann_prune` to remove stale cache files and enforce cache size
limits from MCP. Use `semantic_launchd_status` when an agent or wrapper UI needs
to check whether the background maintenance plist/script exists, whether it
still runs both maintenance steps, and what the latest maintenance/prune reports
and logs say. The status tool is read-only and does not start or stop launchd.
When `with_launchctl=true`, it runs `launchctl print` only to report whether the
LaunchAgent is currently loaded and what launchd last recorded for `state`,
`runs`, `last_exit_code`, and `run_interval_seconds`. Use
`semantic_launchd_monitor` to persist those snapshots as JSONL history for
long-running stability checks. The monitor summary includes the latest snapshot
age, latest launchd-backed maintenance activity, next expected natural launchd
cycle, and overdue status so agents can tell whether the timer is still inside
its normal interval or has missed a run. Use `semantic_launchd_audit` when an
agent needs a judgement over that history instead of a new sample. It reports
`ok`, `warning`, or `alert` for stale snapshots, overdue natural runs, non-zero
launchd exits, failed maintain/prune reports, stderr output, and consecutive
unhealthy snapshots. Optional notifications are off by default; set
`notify=true` with `notify_on="alert"` or `"warning"` when the MCP client should
surface background-maintenance failures to the user. Use
`semantic_launchd_recover` after an alert to get an explicit recovery plan. It
defaults to dry-run and records the planned actions under reports; only
`apply=true` executes install/bootstrap/kickstart/monitor actions. With
`verify_after_apply=true`, recovery records post-apply status, monitor, and
audit checks and can return `verification_failed` if the follow-up checks do not
pass. Use
`semantic_launchd_trend` to review the accumulated monitor history. It does not
append snapshots; it reports day/hour buckets, observed days, run deltas, and
unhealthy snapshot rates, and labels the evidence `short_window` until enough
days have been observed.
Use `semantic_readiness` when an agent or UI only needs the focused answer:
whether semantic background retrieval is usable now, merely waiting for the
configured multi-day trend window, or needs recovery. It writes a small latest
report under `reports/semantic-readiness-latest.*` and returns the next monitor
due time plus the earliest new-day check time.

If the returned route is wrong:

```text
resolve_alternative_context(
  "告诉我本地所有项目里如何构建个人推荐系统",
  ["<bad path or source_chunk_id from top_sources>"],
  "not the route I wanted",
  12,
  "gitProjects"
)
```

For a fresh folder:

```text
1. build_hot_pack("/Users/gengrf/Downloads", "分析 Downloads 里哪些文件适合进入个人助手长期记忆", true)
2. resolve_context("哪些文件适合进入个人助手长期记忆")
```

Use `search_context` when you already know the exact retrieval query. Use
`resolve_context` when you have a task goal and want the system to choose query
variants and source priorities.
When `retrieval_eval` reports exist, `resolve_context` loads them as a route
selector prior. Use `retrieval_eval_cases` first when the cases came from arena
feedback, so duplicate or malformed raw feedback does not become ranking signal.
Use `bootstrap_runtime=true` only as a system self-test when real user-labeled
arena cases are still sparse; it should not be counted as user preference data.
The compiled prior is persisted in `feedback/route_selector_model.json`, also
written to `resolution_plan.json`, and each source's
`resolver_score_parts.route_selector` explains how much the labeled eval data
changed that candidate's score.

## Security Boundary

The MCP server can read source files that are returned by the index and can write
only generated outputs under the configured `--out` root. It does not modify the
scanned source scope. Keep `--out` pointed at a trusted local checkout.
If `read_source` receives a raw path that is not present in an index or provider
manifest, it only reads files under `--out`; arbitrary absolute path reads are
rejected.

Access policy is configured under the output root:

```bash
agent-context access-policy \
  --out /Users/gengrf/agent-context-system \
  --write-default

agent-context access-policy \
  --out /Users/gengrf/agent-context-system \
  --deny-path "*/Secrets/*" \
  --remove-deny-path "*/Secrets/*" \
  --deny-provider claude_session \
  --remove-deny-provider claude_session \
  --require-consent-provider codex_session \
  --require-consent-path "*/PrivateNotes/*"

agent-context access-consent \
  --out /Users/gengrf/agent-context-system \
  --identifier "project:example" \
  --reason "approved for this task"
```

The file is:

```text
config/access_policy.json
```

Policy fields:

```json
{
  "allow_providers": ["direct_text", "markitdown", "metadata_only", "project_code_index", "session_index", "semantic_index", "git_project", "workflow_doc", "codex_session", "claude_session"],
  "deny_providers": [],
  "deny_path_patterns": ["*/.ssh/*", "*/.gnupg/*", "*/Library/Keychains/*", "*.pem", "*.key", "*.env", "*/.npmrc", "*/.netrc"],
  "require_consent_providers": [],
  "require_consent_path_patterns": [],
  "audit_max_bytes": 5000000,
  "audit_max_rotated_files": 3
}
```

`deny_path_patterns` use fnmatch against absolute POSIX paths and basenames.
Deny rules win over allow rules. The same policy is applied before resolver
writes sources and before MCP returns `read_source` content. Consent rules are
read-time only: resolver may still return matching sources, but `read_source`
returns a consent-required failure until `grant_access_consent` or
`agent-context access-consent` grants the exact source.

Resolver filtering and MCP reads also append metadata-only audit events:

```text
reports/access_audit.jsonl
```

Use either CLI or MCP to inspect recent events:

```bash
agent-context access-audit \
  --out /Users/gengrf/agent-context-system \
  --limit 50
```

The audit event stores action, decision, identifier, provider, source ids, path,
reason, and summary counts. It does not store extracted source text. Audit
rotation is controlled by `audit_max_bytes` and `audit_max_rotated_files` in
`config/access_policy.json`; rotated history is gzip-compressed as
`reports/access_audit.jsonl.N.gz` and still included by `access-audit`.

The first version is local stdio only. It does not expose HTTP, authentication,
or remote access.
