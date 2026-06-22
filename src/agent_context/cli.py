from __future__ import annotations

import argparse
import json
from pathlib import Path

from .access_policy import load_access_policy, read_access_audit, update_access_policy, write_default_access_policy
from .acceptance import run_v1_acceptance, run_v1_followup, run_v1_refresh, run_v1_stage_status
from .alternatives import resolve_alternative_context
from .answer_review import run_answer_review
from .arena import build_arena, record_feedback
from .clarify import build_clarification
from .codex_hook import build_codex_preflight
from .codebase_memory import build_codebase_memory_index, search_codebase_memory
from .cold_index import build_cold_index, query_cold_index
from .codex_plus_smoke import run_codex_plus_smoke
from .compare import compare_routes
from .context_review import run_context_review
from .embedding_benchmark import run_embedding_benchmark
from .execution_review import run_execution_review
from .evidence_index import build_evidence_index, search_evidence_index
from .feedback_model import write_feedback_model
from .feedback_replay import run_feedback_replay
from .feedback_replay_cases import run_feedback_replay_case_maintenance
from .feedback_replay_trend import run_feedback_replay_trend
from .ingest import ingest_scope, write_report, IngestPaths
from .io import read_jsonl
from .lab import run_lab
from .launchd import (
    run_semantic_launchd,
    run_semantic_launchd_audit,
    run_semantic_launchd_monitor,
    run_semantic_launchd_recover,
    run_semantic_launchd_trend,
    semantic_launchd_status,
    wait_for_semantic_launchd_run,
)
from .mcp_live_smoke import run_mcp_live_smoke
from .mcp_server import mcp_grant_access_consent, run_mcp_server
from .pack import build_context_pack
from .panel import build_context_panel, record_panel_feedback
from .project_index import build_project_index
from .providers import refresh_projects, refresh_providers
from .reproducibility import run_reproducibility_snapshot
from .resolver import resolve_context
from .retrieval_eval import run_retrieval_eval
from .retrieval_eval_cases import run_retrieval_eval_case_maintenance
from .route_selector import write_route_selector_model
from .runtime_health import run_runtime_health, run_semantic_readiness
from .runtime_vm import inspect_runtime_session, run_runtime_vm_acceptance, start_runtime_session
from .semantic_index import run_semantic_refresh, semantic_index_status
from .semantic_maintenance import run_semantic_ann_prune, run_semantic_maintenance
from .semantic import semantic_status
from .session_index import build_session_index


SOURCE_SCOPE_CHOICES = ["downloads", "gitProjects", "codebaseMemory", "codexSessions", "agentSessions", "workflowDocs", "all"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-context")
    parser.add_argument("--out", dest="global_out", default=".", help="Output root. Defaults to the current directory.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Scan a scope and build extracted Markdown plus manifests.")
    ingest.add_argument("--scope", required=True, help="File or directory scope to scan.")
    ingest.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    pack = subparsers.add_parser("pack", help="Build a hot context pack from existing manifests.")
    pack.add_argument("--scope", required=True, help="Original scan scope.")
    pack.add_argument("--goal", required=True, help="Task goal for ranking context.")
    pack.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    report = subparsers.add_parser("report", help="Regenerate the ingestion report from manifests.")
    report.add_argument("--scope", required=True, help="Original scan scope.")
    report.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    build = subparsers.add_parser("build", help="Run ingest, then generate a hot context pack.")
    build.add_argument("--scope", required=True, help="File or directory scope to scan.")
    build.add_argument("--goal", required=True, help="Task goal for ranking context.")
    build.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    build.add_argument(
        "--with-index",
        action="store_true",
        help="Also rebuild the SQLite cold index from generated manifests.",
    )

    index = subparsers.add_parser("index", help="Build the SQLite cold index from existing manifests.")
    index.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    query = subparsers.add_parser("query", help="Query the cold index and write a RAG context pack.")
    query.add_argument("--query", required=True, help="Natural-language or keyword query.")
    query.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    query.add_argument("--limit", type=int, default=12, help="Maximum sources to return.")

    evidence_index = subparsers.add_parser("evidence-index", help="Build indexes/evidence.sqlite from provider manifests and generated packs.")
    evidence_index.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    evidence_search = subparsers.add_parser("evidence-search", help="Search the unified evidence index.")
    evidence_search.add_argument("--query", required=True, help="Natural-language or keyword query.")
    evidence_search.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    evidence_search.add_argument("--limit", type=int, default=12, help="Maximum evidence records to return.")

    clarify = subparsers.add_parser("clarify", help="Normalize a user task without reading Doctor indexes.")
    clarify.add_argument("--goal", required=True, help="Original user task to normalize for review.")
    clarify.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    clarify.add_argument("--session-id", default=None, help="Optional runtime session id to reuse.")
    clarify.add_argument("--mode", choices=["fast", "standard"], default="standard", help="Clarification mode metadata.")

    doctor_run = subparsers.add_parser("run", help="Start a Doctor runtime session with no-index clarification.")
    doctor_run.add_argument("--goal", required=True, help="Original user task to normalize for review.")
    doctor_run.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    doctor_run.add_argument("--session-id", default=None, help="Optional runtime session id to reuse.")
    doctor_run.add_argument("--mode", choices=["fast", "standard"], default="standard", help="Clarification mode metadata.")

    doctor_session = subparsers.add_parser("session", help="Inspect a Doctor runtime session and write DOCTOR_SESSION.md.")
    doctor_session.add_argument("--session-id", required=True, help="Runtime session id to inspect.")
    doctor_session.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    runtime_acceptance = subparsers.add_parser("runtime-acceptance", help="Write a Doctor runtime VM acceptance handoff report.")
    runtime_acceptance.add_argument("--session-id", required=True, help="Runtime session id to verify.")
    runtime_acceptance.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    resolve = subparsers.add_parser("resolve", help="Resolve a task goal into a hot context pack.")
    resolve.add_argument("--goal", required=True, help="Task goal to resolve into relevant local context.")
    resolve.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    resolve.add_argument("--limit", type=int, default=12, help="Maximum sources to include.")
    resolve.add_argument(
        "--source-scope",
        choices=SOURCE_SCOPE_CHOICES,
        default="all",
        help="Restrict resolver source families. Defaults to all.",
    )

    lab = subparsers.add_parser("lab", help="Open a dedicated Doctor test console for text/image prompts and feedback.")
    lab.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    lab.add_argument("--text", default=None, help="Run one lab prompt without entering interactive mode.")
    lab.add_argument("--image", action="append", default=None, help="Attach an image path to the lab prompt. Can be passed more than once.")
    lab.add_argument("--once", action="store_true", help="Run once and exit, even when only attachments are provided.")
    lab.add_argument("--limit", type=int, default=8, help="Maximum sources to include.")
    lab.add_argument(
        "--source-scope",
        choices=SOURCE_SCOPE_CHOICES,
        default="all",
        help="Restrict resolver source families. Defaults to all.",
    )

    resolve_alternative = subparsers.add_parser("resolve-alternative", help="Record rejected sources and generate an alternative hot context pack.")
    resolve_alternative.add_argument("--goal", required=True, help="Task goal to resolve into relevant local context.")
    resolve_alternative.add_argument("--reject-source", action="append", required=True, help="Rejected source path/id/chunk id. Can be passed more than once.")
    resolve_alternative.add_argument("--reason", default="", help="Optional reason for rejecting the source.")
    resolve_alternative.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    resolve_alternative.add_argument("--limit", type=int, default=12, help="Maximum sources to include.")
    resolve_alternative.add_argument(
        "--source-scope",
        choices=SOURCE_SCOPE_CHOICES,
        default="all",
        help="Restrict resolver source families. Defaults to all.",
    )

    codex_preflight = subparsers.add_parser("codex-preflight", help="Generate a Codex-readable context preflight for a task goal.")
    codex_preflight.add_argument("--goal", required=True, help="Task goal to resolve before Codex starts work.")
    codex_preflight.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    codex_preflight.add_argument("--limit", type=int, default=12, help="Maximum sources to include.")
    codex_preflight.add_argument(
        "--source-scope",
        choices=SOURCE_SCOPE_CHOICES,
        default="all",
        help="Restrict resolver source families. Defaults to all.",
    )
    codex_preflight.add_argument("--mode", choices=["fast", "deep", "arena"], default="fast", help="Preflight mode metadata.")
    codex_preflight.add_argument("--no-auto-context", action="store_true", help="Do not call resolver; emit disabled preflight metadata.")

    context_review = subparsers.add_parser("context-review", help="Generate or record review decisions for Doctor model_input.md.")
    context_review.add_argument("--action", choices=["generate", "regenerate", "approve", "reject"], default="generate", help="Context review action.")
    context_review.add_argument("--refined-prompt", default=None, help="Path to refined_prompt.md from agent-context clarify.")
    context_review.add_argument("--session-id", default=None, help="Runtime session id. Required for approve/reject; optional for generate.")
    context_review.add_argument("--reason", default="", help="Optional review or regeneration reason.")
    context_review.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    context_review.add_argument("--limit", type=int, default=12, help="Maximum sources to include for generate/regenerate.")
    context_review.add_argument(
        "--source-scope",
        choices=SOURCE_SCOPE_CHOICES,
        default="all",
        help="Restrict resolver source families for generate/regenerate.",
    )
    context_review.add_argument("--mode", choices=["fast", "deep", "arena"], default="fast", help="Preflight mode for generate/regenerate.")

    answer_review = subparsers.add_parser("answer-review", help="Prepare, record, or review an answer from an approved Doctor model input.")
    answer_review.add_argument("--action", choices=["prepare", "record", "approve", "reject"], default="prepare", help="Answer review action.")
    answer_review.add_argument("--session-id", required=True, help="Runtime session id.")
    answer_review.add_argument("--answer-text", default="", help="Inline answer text for --action record.")
    answer_review.add_argument("--answer-file", default=None, help="Path to answer Markdown/text for --action record.")
    answer_review.add_argument("--reason", default="", help="Optional preparation or review reason.")
    answer_review.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    execution_review = subparsers.add_parser("execution-review", help="Prepare, run, record, or review local execution artifacts.")
    execution_review.add_argument("--action", choices=["prepare", "run", "record", "approve", "reject"], default="prepare", help="Execution review action.")
    execution_review.add_argument("--session-id", required=True, help="Runtime session id.")
    execution_review.add_argument("--command", dest="execution_command", default="", help="Command to run for --action run. Parsed without shell expansion.")
    execution_review.add_argument("--cwd", default=None, help="Working directory for --action run. Defaults to --out.")
    execution_review.add_argument("--timeout-seconds", type=int, default=120, help="Maximum seconds for --action run.")
    execution_review.add_argument("--artifact-file", default=None, help="Path to an externally generated artifact for --action record.")
    execution_review.add_argument("--reason", default="", help="Optional preparation, run, or review reason.")
    execution_review.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    panel = subparsers.add_parser("panel", help="Write Context Panel status JSON and a local HTML panel.")
    panel.add_argument("--goal", default=None, help="Optional task goal; when provided, run Codex preflight before writing panel status.")
    panel.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    panel.add_argument("--limit", type=int, default=12, help="Maximum sources to include when --goal is provided.")
    panel.add_argument(
        "--source-scope",
        choices=SOURCE_SCOPE_CHOICES,
        default="all",
        help="Restrict resolver source families when --goal is provided.",
    )
    panel.add_argument("--mode", choices=["fast", "deep", "arena"], default="fast", help="Panel/preflight mode metadata.")
    panel.add_argument("--no-auto-context", action="store_true", help="Write panel status without running resolver.")

    panel_feedback = subparsers.add_parser("panel-feedback", help="Record Context Panel source feedback.")
    panel_feedback.add_argument("--source", required=True, help="Selected source path/id to rate.")
    panel_feedback.add_argument("--rating", required=True, help="Rating such as useful, irrelevant, 5, or 1.")
    panel_feedback.add_argument("--reason", default="", help="Optional free-text reason.")
    panel_feedback.add_argument("--status", default=None, help="Optional panel/status.json path.")
    panel_feedback.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    providers = subparsers.add_parser("providers", help="Refresh project, agent session, and workflow provider manifests.")
    providers.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    providers.add_argument(
        "--project-root",
        action="append",
        default=None,
        help="Project discovery root. Can be passed more than once. Defaults to the home directory.",
    )
    providers.add_argument("--sessions-root", default=None, help="Codex sessions root. Defaults to ~/.codex/sessions.")
    providers.add_argument("--claude-root", default=None, help="Claude sessions root. Defaults to ~/.claude/projects.")
    providers.add_argument(
        "--workflow-root",
        action="append",
        default=None,
        help="Workflow Markdown discovery root. Can be passed more than once. Defaults to the output root.",
    )
    providers.add_argument("--max-projects", type=int, default=300, help="Maximum project cards to write.")
    providers.add_argument("--max-sessions", type=int, default=300, help="Maximum session cards to write.")
    providers.add_argument("--max-workflows", type=int, default=300, help="Maximum workflow cards to write.")

    discover_projects = subparsers.add_parser("discover-projects", help="Refresh only project discovery cards.")
    discover_projects.add_argument("--scope", action="append", required=True, help="Project discovery root. Can be passed more than once.")
    discover_projects.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    discover_projects.add_argument("--max-projects", type=int, default=300, help="Maximum project cards to write.")

    index_projects = subparsers.add_parser("index-projects", help="Index project README/docs/source files into indexes/projects.sqlite.")
    index_projects.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    index_projects.add_argument("--project-root", action="append", default=None, help="Refresh project discovery from this root before indexing.")
    index_projects.add_argument("--max-projects", type=int, default=300, help="Maximum projects to index.")
    index_projects.add_argument("--max-files-per-project", type=int, default=300, help="Maximum files to index per project.")

    codebase_memory_index = subparsers.add_parser(
        "codebase-memory-index",
        help="Build the Doctor extracted Markdown pseudo repo and optionally index it with codebase-memory-mcp.",
    )
    codebase_memory_index.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    codebase_memory_index.add_argument("--repo-path", action="append", default=None, help="Extra repository path to index with codebase-memory-mcp. Can be passed more than once.")
    codebase_memory_index.add_argument("--binary", default=None, help="Optional codebase-memory-mcp binary path. Defaults to PATH or AGENT_CONTEXT_CODEBASE_MEMORY_BIN.")
    codebase_memory_index.add_argument("--timeout-seconds", type=int, default=120, help="Maximum seconds per external tool call.")

    codebase_memory_search = subparsers.add_parser(
        "codebase-memory-search",
        help="Search the optional codebase-memory-mcp provider and print Doctor-shaped sources.",
    )
    codebase_memory_search.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    codebase_memory_search.add_argument("--query", required=True, help="Search query.")
    codebase_memory_search.add_argument("--limit", type=int, default=12, help="Maximum sources to return.")
    codebase_memory_search.add_argument("--binary", default=None, help="Optional codebase-memory-mcp binary path. Defaults to PATH or AGENT_CONTEXT_CODEBASE_MEMORY_BIN.")
    codebase_memory_search.add_argument("--timeout-seconds", type=int, default=120, help="Maximum seconds per external tool call.")

    index_sessions = subparsers.add_parser("index-sessions", help="Index Codex/Claude session transcript previews into indexes/sessions.sqlite.")
    index_sessions.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    index_sessions.add_argument("--max-sessions", type=int, default=300, help="Maximum session provider cards to index.")
    index_sessions.add_argument("--max-messages-per-session", type=int, default=1000, help="Maximum readable messages per session transcript preview.")

    compare = subparsers.add_parser("compare", help="Run Route A and Route B context pack experiments.")
    compare.add_argument("--scope", required=True, help="File or directory scope to scan.")
    compare.add_argument("--goal", required=True, help="Task goal for ranking context.")
    compare.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    compare.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Reuse existing manifests instead of scanning the scope before comparing routes.",
    )

    arena = subparsers.add_parser("arena", help="Generate a three-candidate arena slate for user selection.")
    arena.add_argument("--scope", required=True, help="File or directory scope to scan.")
    arena.add_argument("--goal", required=True, help="Task goal for generating candidate answers.")
    arena.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    arena.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Reuse existing manifests instead of scanning the scope before generating the arena slate.",
    )

    feedback = subparsers.add_parser("feedback", help="Record the user's arena candidate choice.")
    feedback.add_argument("--slate", required=True, help="Path to an arena slate.json file.")
    feedback.add_argument("--winner", required=True, help="Winning candidate id, for example candidate-2.")
    feedback.add_argument("--reason", default="", help="Optional free-text reason for the choice.")

    feedback_model = subparsers.add_parser("feedback-model", help="Compile feedback JSONL into feedback/model.json.")
    feedback_model.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    feedback_replay = subparsers.add_parser("feedback-replay", help="Replay fixed goals before/after feedback rerank.")
    feedback_replay.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    feedback_replay.add_argument("--cases", default=None, help="Optional JSONL replay cases file. Defaults to manual plus generated replay cases.")
    feedback_replay.add_argument("--case", action="append", default=None, help="Inline replay goal. Can be passed more than once.")
    feedback_replay.add_argument("--limit", type=int, default=12, help="Maximum sources to compare per case.")
    feedback_replay.add_argument(
        "--source-scope",
        choices=SOURCE_SCOPE_CHOICES,
        default="all",
        help="Default source scope for inline cases.",
    )

    feedback_replay_cases = subparsers.add_parser("feedback-replay-cases", help="Generate replay cases from feedback logs.")
    feedback_replay_cases.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    feedback_replay_cases.add_argument("--output-cases", default=None, help="Generated JSONL destination. Defaults to feedback/replay_cases.generated.jsonl.")
    feedback_replay_cases.add_argument("--limit", type=int, default=12, help="Maximum sources to compare per generated case.")
    feedback_replay_cases.add_argument(
        "--source-scope",
        choices=SOURCE_SCOPE_CHOICES,
        default="all",
        help="Default source scope when a feedback record does not imply one.",
    )

    feedback_replay_trend = subparsers.add_parser("feedback-replay-trend", help="Summarize feedback replay report history and health.")
    feedback_replay_trend.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    feedback_replay_trend.add_argument("--max-reports", type=int, default=20, help="Maximum latest feedback_replay reports to include.")
    feedback_replay_trend.add_argument("--min-reports", type=int, default=2, help="Minimum reports required for stable history confidence.")

    retrieval_eval = subparsers.add_parser("retrieval-eval", help="Evaluate retrieval backends against labeled expected sources.")
    retrieval_eval.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    retrieval_eval.add_argument("--cases", default=None, help="Optional JSONL eval cases file. Defaults to curated feedback cases when present, then raw feedback cases.")
    retrieval_eval.add_argument("--case", action="append", default=None, help='Inline case: "query => expected source". Can be passed more than once.')
    retrieval_eval.add_argument("--source", choices=["downloads", "projects"], default="projects", help="Default source family for inline/default cases.")
    retrieval_eval.add_argument("--limit", type=int, default=8, help="Maximum sources to evaluate per backend/query.")

    retrieval_eval_cases = subparsers.add_parser("retrieval-eval-cases", help="Curate raw feedback into retrieval eval cases.")
    retrieval_eval_cases.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    retrieval_eval_cases.add_argument("--cases", default=None, help="Optional raw JSONL cases file. Defaults to feedback/retrieval_eval_cases.jsonl.")
    retrieval_eval_cases.add_argument("--output-cases", default=None, help="Curated JSONL destination. Defaults to feedback/retrieval_eval_cases.curated.jsonl.")
    retrieval_eval_cases.add_argument("--max-age-days", type=int, default=0, help="Drop raw cases older than this many days. 0 disables age filtering.")
    retrieval_eval_cases.add_argument("--source", choices=["downloads", "projects"], default="projects", help="Default source family when a raw case omits source.")
    retrieval_eval_cases.add_argument(
        "--bootstrap-runtime",
        action="store_true",
        help="Include labeled system self-test cases for indexed agent-context runtime source files.",
    )

    route_selector_model = subparsers.add_parser("route-selector-model", help="Compile retrieval eval reports into feedback/route_selector_model.json.")
    route_selector_model.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    route_selector_model.add_argument("--max-reports", type=int, default=50, help="Maximum latest retrieval_eval_*.json reports to compile.")

    runtime_health = subparsers.add_parser("runtime-health", help="Write a v1 runtime health and acceptance evidence report.")
    runtime_health.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    runtime_health.add_argument("--codex-plus-root", default=None, help="Optional Codex++ repo root for integration evidence.")
    runtime_health.add_argument("--min-documents", type=int, default=1, help="Minimum Downloads documents expected.")
    runtime_health.add_argument("--min-projects", type=int, default=1, help="Minimum project provider cards expected.")
    runtime_health.add_argument("--min-sessions", type=int, default=1, help="Minimum session provider cards expected.")
    runtime_health.add_argument("--min-workflows", type=int, default=1, help="Minimum workflow provider cards expected.")
    runtime_health.add_argument("--min-semantic-chunks", type=int, default=16, help="Minimum semantic chunks expected before semantic fusion is trusted.")

    v1_acceptance = subparsers.add_parser("v1-acceptance", help="Write a single v1 acceptance handoff report from latest runtime evidence.")
    v1_acceptance.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    v1_acceptance.add_argument("--codex-plus-root", default=None, help="Optional Codex++ repo root for integration and reproducibility evidence.")
    v1_acceptance.add_argument("--refresh-health", action="store_true", help="Refresh runtime-health and semantic-readiness before writing the acceptance report.")
    v1_acceptance.add_argument(
        "--refresh-evidence",
        action="store_true",
        help="Refresh semantic trend, semantic readiness, reproducibility snapshot, MCP live smoke, and runtime-health before writing the acceptance report.",
    )
    v1_acceptance.add_argument("--min-documents", type=int, default=1, help="Minimum Downloads documents expected when refreshing health.")
    v1_acceptance.add_argument("--min-projects", type=int, default=1, help="Minimum project provider cards expected when refreshing health.")
    v1_acceptance.add_argument("--min-sessions", type=int, default=1, help="Minimum session provider cards expected when refreshing health.")
    v1_acceptance.add_argument("--min-workflows", type=int, default=1, help="Minimum workflow provider cards expected when refreshing health.")
    v1_acceptance.add_argument("--min-semantic-chunks", type=int, default=16, help="Minimum semantic chunks expected before semantic fusion is trusted.")
    v1_acceptance.add_argument("--required-trend-days", type=int, default=2, help="Observed semantic launchd days required before v1 is ready.")
    v1_acceptance.add_argument("--mcp-timeout-seconds", type=int, default=60, help="Maximum seconds for MCP live smoke when --refresh-evidence is used.")
    v1_acceptance.add_argument("--codex-plus-timeout-seconds", type=int, default=120, help="Maximum seconds for Codex++ smoke when --refresh-evidence is used.")
    v1_acceptance.add_argument(
        "--with-manager-feedback-smoke",
        action="store_true",
        help="When refreshing evidence, also run the Codex++ Manager feedback replay contract smoke.",
    )

    v1_followup = subparsers.add_parser("v1-followup", help="Check or safely run the v1 acceptance follow-up gate.")
    v1_followup.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    v1_followup.add_argument("--codex-plus-root", default=None, help="Optional Codex++ repo root for follow-up acceptance evidence.")
    v1_followup.add_argument("--run-when-ready", action="store_true", help="Run v1-acceptance --refresh-evidence only when the follow-up gate says recheck is due.")
    v1_followup.add_argument("--force", action="store_true", help="Run v1-acceptance --refresh-evidence now, even before earliest_recheck_after.")
    v1_followup.add_argument("--min-documents", type=int, default=1, help="Minimum Downloads documents expected when refreshing evidence.")
    v1_followup.add_argument("--min-projects", type=int, default=1, help="Minimum project provider cards expected when refreshing evidence.")
    v1_followup.add_argument("--min-sessions", type=int, default=1, help="Minimum session provider cards expected when refreshing evidence.")
    v1_followup.add_argument("--min-workflows", type=int, default=1, help="Minimum workflow provider cards expected when refreshing evidence.")
    v1_followup.add_argument("--min-semantic-chunks", type=int, default=16, help="Minimum semantic chunks expected before semantic fusion is trusted.")
    v1_followup.add_argument("--required-trend-days", type=int, default=2, help="Observed semantic launchd days required before v1 is ready.")
    v1_followup.add_argument("--mcp-timeout-seconds", type=int, default=60, help="Maximum seconds for MCP live smoke when refreshing evidence.")
    v1_followup.add_argument("--codex-plus-timeout-seconds", type=int, default=120, help="Maximum seconds for Codex++ smoke when refreshing evidence.")
    v1_followup.add_argument(
        "--with-manager-feedback-smoke",
        action="store_true",
        help="When refreshing evidence, also run the Codex++ Manager feedback replay contract smoke.",
    )

    v1_refresh = subparsers.add_parser("v1-refresh", help="Safely refresh v1 follow-up, stage status, and panel status.")
    v1_refresh.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    v1_refresh.add_argument("--codex-plus-root", default=None, help="Optional Codex++ repo root for refresh evidence.")
    v1_refresh.add_argument("--force", action="store_true", help="Run heavy v1 acceptance refresh even before the follow-up gate is due.")
    v1_refresh.add_argument(
        "--no-refresh-semantic-evidence",
        action="store_true",
        help="Skip semantic monitor/audit/trend/readiness refresh even when the evidence gate is due.",
    )
    v1_refresh.add_argument(
        "--no-refresh-mcp-smoke",
        action="store_true",
        help="Skip the MCP live smoke refresh before syncing runtime health, stage status, and panel.",
    )
    v1_refresh.add_argument(
        "--no-refresh-runtime-health",
        action="store_true",
        help="Skip the lightweight runtime-health refresh before syncing stage status and panel.",
    )
    v1_refresh.add_argument("--min-documents", type=int, default=1, help="Minimum Downloads documents expected when refreshing evidence.")
    v1_refresh.add_argument("--min-projects", type=int, default=1, help="Minimum project provider cards expected when refreshing evidence.")
    v1_refresh.add_argument("--min-sessions", type=int, default=1, help="Minimum session provider cards expected when refreshing evidence.")
    v1_refresh.add_argument("--min-workflows", type=int, default=1, help="Minimum workflow provider cards expected when refreshing evidence.")
    v1_refresh.add_argument("--min-semantic-chunks", type=int, default=16, help="Minimum semantic chunks expected before semantic fusion is trusted.")
    v1_refresh.add_argument("--required-trend-days", type=int, default=2, help="Observed semantic launchd days required before v1 is ready.")
    v1_refresh.add_argument("--mcp-timeout-seconds", type=int, default=60, help="Maximum seconds for MCP live smoke when refreshing evidence.")
    v1_refresh.add_argument("--codex-plus-timeout-seconds", type=int, default=120, help="Maximum seconds for Codex++ smoke when refreshing evidence.")
    v1_refresh.add_argument(
        "--wait-for-semantic-evidence",
        action="store_true",
        help="If the semantic evidence gate is not due yet, wait for the next natural semantic LaunchAgent run before refreshing.",
    )
    v1_refresh.add_argument("--semantic-wait-timeout-seconds", type=int, default=7200, help="Maximum seconds to wait for semantic LaunchAgent evidence.")
    v1_refresh.add_argument("--semantic-wait-poll-seconds", type=int, default=60, help="Polling interval while waiting for semantic LaunchAgent evidence.")
    v1_refresh.add_argument(
        "--with-manager-feedback-smoke",
        action="store_true",
        help="When refreshing evidence, also run the Codex++ Manager feedback replay contract smoke.",
    )

    v1_stage_status = subparsers.add_parser("v1-stage-status", help="Write a compact v1 stage progress report from latest evidence.")
    v1_stage_status.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    v1_stage_status.add_argument("--codex-plus-root", default=None, help="Optional Codex++ repo root for report metadata.")

    codex_plus_smoke = subparsers.add_parser("codex-plus-smoke", help="Run Codex++ Agent Context smoke scripts and write a report.")
    codex_plus_smoke.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    codex_plus_smoke.add_argument("--codex-plus-root", default=None, help="Codex++ repo root. Defaults to the known local checkout.")
    codex_plus_smoke.add_argument("--timeout-seconds", type=int, default=120, help="Maximum seconds per smoke script.")
    codex_plus_smoke.add_argument("--with-manager-feedback", action="store_true", help="Also run the Manager feedback replay contract smoke.")
    codex_plus_smoke.add_argument("--with-runtime", action="store_true", help="Also run the Codex++ GUI runtime smoke; this may launch Codex.app.")

    semantic_readiness = subparsers.add_parser("semantic-readiness", help="Write a focused semantic background readiness report.")
    semantic_readiness.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_readiness.add_argument("--min-semantic-chunks", type=int, default=16, help="Minimum semantic chunks expected before semantic fusion is trusted.")
    semantic_readiness.add_argument("--required-trend-days", type=int, default=2, help="Observed days required before background semantic trend is ready.")
    semantic_readiness.add_argument("--label", default="com.gengrf.agent-context.semantic-maintenance", help="LaunchAgent label.")
    semantic_readiness.add_argument("--launch-agents-dir", default=None, help="Override LaunchAgents directory; useful for tests.")
    semantic_readiness.add_argument("--with-launchctl", action="store_true", help="Also run read-only launchctl print to report loaded state.")

    reproducibility_snapshot = subparsers.add_parser("reproducibility-snapshot", help="Write a git worktree reproducibility snapshot for dirty local v1 changes.")
    reproducibility_snapshot.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    reproducibility_snapshot.add_argument("--codex-plus-root", default=None, help="Optional Codex++ repo root to include.")

    semantic_status_parser = subparsers.add_parser("semantic-status", help="Report available embedding/ANN/rerank backends.")
    semantic_status_parser.add_argument("--out", default=None, help="Accepted for symmetry; not used by this command.")

    semantic_refresh = subparsers.add_parser("semantic-refresh", help="Run a budgeted background semantic embedding refresh job.")
    semantic_refresh.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_refresh.add_argument("--source", choices=["downloads", "projects", "sessions", "all"], default="all", help="Source index family to refresh.")
    semantic_refresh.add_argument("--budget", type=int, default=32, help="Maximum new chunks to embed in this job.")
    semantic_refresh.add_argument("--backend", default="fastembed", help="Embedding backend for semantic chunks.")
    semantic_refresh.add_argument("--text-chars", type=int, default=800, help="Maximum text chars per candidate embedding input.")

    semantic_maintain = subparsers.add_parser("semantic-maintain", help="Run one or more budgeted semantic refresh jobs and write an auditable report.")
    semantic_maintain.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_maintain.add_argument("--source", choices=["downloads", "projects", "sessions", "all"], default="all", help="Source index family to maintain.")
    semantic_maintain.add_argument("--budget", type=int, default=32, help="Maximum new chunks to embed per refresh job.")
    semantic_maintain.add_argument("--backend", default="fastembed", help="Embedding backend for semantic chunks.")
    semantic_maintain.add_argument("--text-chars", type=int, default=800, help="Maximum text chars per candidate embedding input.")
    semantic_maintain.add_argument("--max-jobs", type=int, default=1, help="Maximum refresh jobs to run in this maintenance pass.")
    semantic_maintain.add_argument(
        "--min-interval-minutes",
        type=int,
        default=0,
        help="Skip this maintenance pass when the latest semantic job is newer than this interval.",
    )

    semantic_index_status_parser = subparsers.add_parser("semantic-index-status", help="Report background semantic index progress.")
    semantic_index_status_parser.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    semantic_benchmark = subparsers.add_parser("semantic-benchmark", help="Compare hash, rerank, and semantic-fusion retrieval modes.")
    semantic_benchmark.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_benchmark.add_argument("--source", choices=["downloads", "projects"], default="projects", help="Manifest/index family to benchmark.")
    semantic_benchmark.add_argument("--query", action="append", required=True, help="Benchmark query. Can be passed more than once.")
    semantic_benchmark.add_argument("--limit", type=int, default=8, help="Maximum sources returned per backend/query.")

    semantic_ann_prune = subparsers.add_parser("semantic-ann-prune", help="Prune stale or excessive indexes/semantic_ann cache files.")
    semantic_ann_prune.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_ann_prune.add_argument("--max-entries", type=int, default=32, help="Maximum active ANN cache entries to retain.")
    semantic_ann_prune.add_argument("--max-bytes", type=int, default=1_000_000_000, help="Maximum total ANN cache bytes to retain.")
    semantic_ann_prune.add_argument("--dry-run", action="store_true", help="Report removals without deleting files.")

    semantic_launchd = subparsers.add_parser("semantic-launchd", help="Print, install, or uninstall a macOS LaunchAgent for semantic maintenance.")
    semantic_launchd.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_launchd_action = semantic_launchd.add_mutually_exclusive_group()
    semantic_launchd_action.add_argument("--print", dest="launchd_action", action="store_const", const="print", help="Print plist/script without writing files.")
    semantic_launchd_action.add_argument("--install", dest="launchd_action", action="store_const", const="install", help="Write LaunchAgent plist and maintenance script.")
    semantic_launchd_action.add_argument("--uninstall", dest="launchd_action", action="store_const", const="uninstall", help="Remove the LaunchAgent plist and maintenance script.")
    semantic_launchd.set_defaults(launchd_action="print")
    semantic_launchd.add_argument("--label", default="com.gengrf.agent-context.semantic-maintenance", help="LaunchAgent label.")
    semantic_launchd.add_argument("--interval-minutes", type=int, default=60, help="LaunchAgent StartInterval in minutes.")
    semantic_launchd.add_argument("--source", choices=["downloads", "projects", "sessions", "all"], default="all", help="Source family for semantic-maintain.")
    semantic_launchd.add_argument("--budget", type=int, default=32, help="Maximum new chunks per semantic-maintain job.")
    semantic_launchd.add_argument("--max-jobs", type=int, default=2, help="Maximum semantic-maintain jobs per launchd run.")
    semantic_launchd.add_argument("--min-interval-minutes", type=int, default=30, help="Skip maintenance when latest semantic job is newer than this.")
    semantic_launchd.add_argument("--ann-max-entries", type=int, default=32, help="Maximum ANN cache entries retained by prune.")
    semantic_launchd.add_argument("--ann-max-bytes", type=int, default=1_000_000_000, help="Maximum ANN cache bytes retained by prune.")
    semantic_launchd.add_argument("--agent-context-bin", default="agent-context", help="agent-context executable used by the generated script.")
    semantic_launchd.add_argument("--launch-agents-dir", default=None, help="Override LaunchAgents directory; useful for tests.")

    semantic_launchd_status_parser = subparsers.add_parser("semantic-launchd-status", help="Read semantic LaunchAgent installation, report, and log status.")
    semantic_launchd_status_parser.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_launchd_status_parser.add_argument("--label", default="com.gengrf.agent-context.semantic-maintenance", help="LaunchAgent label.")
    semantic_launchd_status_parser.add_argument("--launch-agents-dir", default=None, help="Override LaunchAgents directory; useful for tests.")
    semantic_launchd_status_parser.add_argument("--tail-lines", type=int, default=20, help="Number of stdout/stderr log lines to include.")
    semantic_launchd_status_parser.add_argument("--with-launchctl", action="store_true", help="Also run read-only launchctl print to report loaded state.")

    semantic_launchd_monitor = subparsers.add_parser("semantic-launchd-monitor", help="Append a semantic LaunchAgent health snapshot and write latest monitor reports.")
    semantic_launchd_monitor.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_launchd_monitor.add_argument("--label", default="com.gengrf.agent-context.semantic-maintenance", help="LaunchAgent label.")
    semantic_launchd_monitor.add_argument("--launch-agents-dir", default=None, help="Override LaunchAgents directory; useful for tests.")
    semantic_launchd_monitor.add_argument("--tail-lines", type=int, default=20, help="Number of stdout/stderr log lines to include in the snapshot.")
    semantic_launchd_monitor.add_argument("--with-launchctl", action="store_true", help="Run read-only launchctl print and include loaded/runs/exit state.")
    semantic_launchd_monitor.add_argument("--max-history", type=int, default=200, help="Maximum recent snapshots to summarize.")

    semantic_launchd_wait = subparsers.add_parser("semantic-launchd-wait", help="Wait for a natural semantic LaunchAgent run and write a wait report.")
    semantic_launchd_wait.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_launchd_wait.add_argument("--label", default="com.gengrf.agent-context.semantic-maintenance", help="LaunchAgent label.")
    semantic_launchd_wait.add_argument("--launch-agents-dir", default=None, help="Override LaunchAgents directory; useful for tests.")
    semantic_launchd_wait.add_argument("--tail-lines", type=int, default=20, help="Number of stdout/stderr log lines to include in snapshots.")
    semantic_launchd_wait.add_argument("--no-launchctl", dest="with_launchctl", action="store_false", help="Do not call launchctl print while polling.")
    semantic_launchd_wait.set_defaults(with_launchctl=True)
    semantic_launchd_wait.add_argument("--max-history", type=int, default=200, help="Maximum recent snapshots to summarize.")
    semantic_launchd_wait.add_argument("--timeout-seconds", type=int, default=7200, help="Maximum seconds to wait.")
    semantic_launchd_wait.add_argument("--poll-seconds", type=int, default=60, help="Polling interval while waiting.")

    semantic_launchd_audit = subparsers.add_parser("semantic-launchd-audit", help="Audit semantic LaunchAgent monitor history and write a health report.")
    semantic_launchd_audit.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_launchd_audit.add_argument("--max-history", type=int, default=200, help="Maximum recent monitor snapshots to audit.")
    semantic_launchd_audit.add_argument("--min-snapshots", type=int, default=2, help="Minimum snapshots before stability can be trusted.")
    semantic_launchd_audit.add_argument("--consecutive-unhealthy-threshold", type=int, default=3, help="Consecutive unhealthy snapshots that should trigger an alert.")
    semantic_launchd_audit.add_argument("--max-snapshot-age-seconds", type=int, default=None, help="Maximum allowed age for latest monitor snapshot; defaults to two launchd intervals plus grace.")
    semantic_launchd_audit.add_argument("--notify", action="store_true", help="Send a macOS notification when the audit health meets --notify-on.")
    semantic_launchd_audit.add_argument("--notify-on", choices=["alert", "warning", "always"], default="alert", help="Minimum audit health that should trigger --notify.")

    semantic_launchd_recover = subparsers.add_parser("semantic-launchd-recover", help="Plan or apply recovery actions for semantic LaunchAgent maintenance.")
    semantic_launchd_recover.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_launchd_recover.add_argument("--apply", action="store_true", help="Apply planned recovery actions. Defaults to dry-run.")
    semantic_launchd_recover.add_argument("--verify-after-apply", action="store_true", help="After --apply, run status/monitor/audit verification and record the result.")
    semantic_launchd_recover.add_argument("--label", default="com.gengrf.agent-context.semantic-maintenance", help="LaunchAgent label.")
    semantic_launchd_recover.add_argument("--launch-agents-dir", default=None, help="Override LaunchAgents directory; useful for tests.")
    semantic_launchd_recover.add_argument("--max-history", type=int, default=200, help="Maximum recent monitor snapshots to audit before planning recovery.")
    semantic_launchd_recover.add_argument("--interval-minutes", type=int, default=60, help="LaunchAgent StartInterval in minutes when install is needed.")
    semantic_launchd_recover.add_argument("--source", choices=["downloads", "projects", "sessions", "all"], default="all", help="Source family for semantic-maintain when install is needed.")
    semantic_launchd_recover.add_argument("--budget", type=int, default=32, help="Maximum new chunks per semantic-maintain job when install is needed.")
    semantic_launchd_recover.add_argument("--max-jobs", type=int, default=2, help="Maximum semantic-maintain jobs per launchd run when install is needed.")
    semantic_launchd_recover.add_argument("--min-interval-minutes", type=int, default=30, help="Skip maintenance when latest semantic job is newer than this.")
    semantic_launchd_recover.add_argument("--ann-max-entries", type=int, default=32, help="Maximum ANN cache entries retained by prune.")
    semantic_launchd_recover.add_argument("--ann-max-bytes", type=int, default=1_000_000_000, help="Maximum ANN cache bytes retained by prune.")
    semantic_launchd_recover.add_argument("--agent-context-bin", default="agent-context", help="agent-context executable used by generated scripts.")

    semantic_launchd_trend = subparsers.add_parser("semantic-launchd-trend", help="Summarize semantic LaunchAgent monitor history across days and hours.")
    semantic_launchd_trend.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    semantic_launchd_trend.add_argument("--max-history", type=int, default=1000, help="Maximum recent monitor snapshots to summarize.")
    semantic_launchd_trend.add_argument("--min-days", type=int, default=2, help="Minimum observed days required for multi-day confidence.")

    access_policy = subparsers.add_parser("access-policy", help="Show or write config/access_policy.json.")
    access_policy.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    access_policy.add_argument("--write-default", action="store_true", help="Write config/access_policy.json if it does not exist.")
    access_policy.add_argument("--overwrite", action="store_true", help="Overwrite config/access_policy.json when used with --write-default.")
    access_policy.add_argument("--allow-provider", action="append", default=None, help="Add a provider to allow_providers.")
    access_policy.add_argument("--remove-allow-provider", action="append", default=None, help="Remove a provider from allow_providers.")
    access_policy.add_argument("--deny-provider", action="append", default=None, help="Add a provider to deny_providers.")
    access_policy.add_argument("--remove-deny-provider", action="append", default=None, help="Remove a provider from deny_providers.")
    access_policy.add_argument("--deny-path", action="append", default=None, help="Add a fnmatch path pattern to deny_path_patterns.")
    access_policy.add_argument("--remove-deny-path", action="append", default=None, help="Remove a path pattern from deny_path_patterns.")
    access_policy.add_argument("--require-consent-provider", action="append", default=None, help="Add a provider that requires explicit read consent.")
    access_policy.add_argument("--remove-require-consent-provider", action="append", default=None, help="Remove a provider from require_consent_providers.")
    access_policy.add_argument("--require-consent-path", action="append", default=None, help="Add a fnmatch path pattern that requires explicit read consent.")
    access_policy.add_argument("--remove-require-consent-path", action="append", default=None, help="Remove a path pattern from require_consent_path_patterns.")
    access_policy.add_argument("--audit-max-bytes", type=int, default=None, help="Set maximum active access audit JSONL size before gzip rotation.")
    access_policy.add_argument("--audit-max-rotated-files", type=int, default=None, help="Set maximum gzip-rotated access audit files to retain.")

    access_consent = subparsers.add_parser("access-consent", help="Grant read consent for an indexed/provider/generated source.")
    access_consent.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    access_consent.add_argument("--identifier", required=True, help="Source id, chunk id, path, or generated artifact to grant.")
    access_consent.add_argument("--reason", default="", help="Optional reason for granting consent.")

    access_audit = subparsers.add_parser("access-audit", help="Show recent reports/access_audit.jsonl events.")
    access_audit.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    access_audit.add_argument("--limit", type=int, default=50, help="Maximum recent audit events to return.")

    mcp = subparsers.add_parser("mcp", help="Run the agent-context MCP server over stdio.")
    mcp.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    mcp_smoke = subparsers.add_parser("mcp-live-smoke", help="Run a real stdio MCP client smoke against agent-context mcp.")
    mcp_smoke.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    mcp_smoke.add_argument("--codex-plus-root", default=None, help="Optional Codex++ repo root passed to runtime_health.")
    mcp_smoke.add_argument("--timeout-seconds", type=int, default=60, help="Maximum seconds for the stdio MCP smoke.")
    mcp_smoke.add_argument(
        "--with-manager-feedback-smoke",
        action="store_true",
        help="Preserve the stronger v1 Manager render-smoke requirement when calling v1 tools.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out_root = Path(getattr(args, "out", None) or args.global_out).expanduser().resolve()

    if args.command == "ingest":
        result = ingest_scope(Path(args.scope), out_root)
    elif args.command == "pack":
        result = build_context_pack(Path(args.scope), out_root, args.goal)
    elif args.command == "report":
        result = regenerate_report(Path(args.scope), out_root)
    elif args.command == "build":
        ingest_result = ingest_scope(Path(args.scope), out_root)
        pack_result = build_context_pack(Path(args.scope), out_root, args.goal)
        result = {"ingest": ingest_result, "pack": pack_result}
        if args.with_index:
            result["index"] = build_cold_index(out_root)
    elif args.command == "index":
        result = build_cold_index(out_root)
    elif args.command == "query":
        result = query_cold_index(out_root, args.query, limit=args.limit)
    elif args.command == "evidence-index":
        result = build_evidence_index(out_root)
    elif args.command == "evidence-search":
        result = search_evidence_index(out_root, args.query, limit=max(1, args.limit))
    elif args.command == "clarify":
        result = build_clarification(out_root, args.goal, session_id=args.session_id, mode=args.mode)
    elif args.command == "run":
        result = start_runtime_session(out_root, args.goal, session_id=args.session_id, mode=args.mode)
    elif args.command == "session":
        result = inspect_runtime_session(out_root, args.session_id)
    elif args.command == "runtime-acceptance":
        result = run_runtime_vm_acceptance(out_root, args.session_id)
    elif args.command == "resolve":
        result = resolve_context(out_root, args.goal, limit=args.limit, source_scope=args.source_scope)
    elif args.command == "lab":
        result = run_lab(
            out_root,
            text=args.text,
            image_paths=args.image or [],
            source_scope=args.source_scope,
            limit=args.limit,
            once=args.once,
        )
    elif args.command == "resolve-alternative":
        result = resolve_alternative_context(
            out_root,
            goal=args.goal,
            rejected_sources=args.reject_source,
            reason=args.reason,
            source_scope=args.source_scope,
            limit=args.limit,
        )
    elif args.command == "codex-preflight":
        result = build_codex_preflight(
            out_root,
            args.goal,
            source_scope=args.source_scope,
            limit=args.limit,
            auto_context=not args.no_auto_context,
            mode=args.mode,
        )
    elif args.command == "context-review":
        result = run_context_review(
            out_root,
            action=args.action,
            refined_prompt_path=args.refined_prompt,
            session_id=args.session_id,
            reason=args.reason,
            source_scope=args.source_scope,
            limit=args.limit,
            mode=args.mode,
        )
    elif args.command == "answer-review":
        try:
            result = run_answer_review(
                out_root,
                action=args.action,
                session_id=args.session_id,
                answer_text=args.answer_text,
                answer_file=args.answer_file,
                reason=args.reason,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "command": "answer-review",
                        "action": args.action,
                        "session_id": args.session_id,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1
    elif args.command == "execution-review":
        try:
            result = run_execution_review(
                out_root,
                action=args.action,
                session_id=args.session_id,
                command=args.execution_command,
                cwd=args.cwd,
                timeout_seconds=args.timeout_seconds,
                artifact_file=args.artifact_file,
                reason=args.reason,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "command": "execution-review",
                        "action": args.action,
                        "session_id": args.session_id,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1
    elif args.command == "panel":
        result = build_context_panel(
            out_root,
            goal=args.goal,
            source_scope=args.source_scope,
            mode=args.mode,
            limit=args.limit,
            auto_context=not args.no_auto_context,
        )
    elif args.command == "panel-feedback":
        result = record_panel_feedback(
            out_root,
            source=args.source,
            rating=args.rating,
            reason=args.reason,
            status_path=args.status,
        )
    elif args.command == "providers":
        project_roots = [Path(value) for value in args.project_root] if args.project_root else None
        sessions_root = Path(args.sessions_root) if args.sessions_root else None
        claude_root = Path(args.claude_root) if args.claude_root else None
        workflow_roots = [Path(value) for value in args.workflow_root] if args.workflow_root else None
        result = refresh_providers(
            out_root,
            project_roots=project_roots,
            sessions_root=sessions_root,
            claude_root=claude_root,
            workflow_roots=workflow_roots,
            max_projects=max(1, args.max_projects),
            max_sessions=max(1, args.max_sessions),
            max_workflows=max(1, args.max_workflows),
        )
    elif args.command == "discover-projects":
        result = refresh_projects(
            out_root,
            project_roots=[Path(value) for value in args.scope],
            max_projects=max(1, args.max_projects),
        )
    elif args.command == "index-projects":
        project_roots = [Path(value) for value in args.project_root] if args.project_root else None
        result = build_project_index(
            out_root,
            project_roots=project_roots,
            max_projects=max(1, args.max_projects),
            max_files_per_project=max(1, args.max_files_per_project),
        )
    elif args.command == "codebase-memory-index":
        repo_paths = [Path(value) for value in args.repo_path] if args.repo_path else None
        result = build_codebase_memory_index(
            out_root,
            repo_paths=repo_paths,
            binary=args.binary,
            timeout_seconds=max(1, args.timeout_seconds),
        )
    elif args.command == "codebase-memory-search":
        result = search_codebase_memory(
            out_root,
            args.query,
            limit=max(1, args.limit),
            binary=args.binary,
            timeout_seconds=max(1, args.timeout_seconds),
        )
    elif args.command == "index-sessions":
        result = build_session_index(
            out_root,
            max_sessions=max(1, args.max_sessions),
            max_messages_per_session=max(1, args.max_messages_per_session),
        )
    elif args.command == "compare":
        result = compare_routes(Path(args.scope), out_root, args.goal, skip_ingest=args.skip_ingest)
    elif args.command == "arena":
        result = build_arena(Path(args.scope), out_root, args.goal, skip_ingest=args.skip_ingest)
    elif args.command == "feedback":
        result = record_feedback(Path(args.slate), args.winner, args.reason)
    elif args.command == "feedback-model":
        result = write_feedback_model(out_root)
    elif args.command == "feedback-replay":
        result = run_feedback_replay(
            out_root,
            cases_path=Path(args.cases) if args.cases else None,
            case_goals=args.case or [],
            source_scope=args.source_scope,
            limit=args.limit,
        )
    elif args.command == "feedback-replay-cases":
        result = run_feedback_replay_case_maintenance(
            out_root,
            output_cases_path=Path(args.output_cases) if args.output_cases else None,
            source_scope=args.source_scope,
            limit=args.limit,
        )
    elif args.command == "feedback-replay-trend":
        result = run_feedback_replay_trend(
            out_root,
            max_reports=max(1, args.max_reports),
            min_reports=max(1, args.min_reports),
        )
    elif args.command == "retrieval-eval":
        result = run_retrieval_eval(
            out_root,
            cases_path=Path(args.cases) if args.cases else None,
            inline_cases=args.case or [],
            source=args.source,
            limit=args.limit,
        )
    elif args.command == "retrieval-eval-cases":
        result = run_retrieval_eval_case_maintenance(
            out_root,
            cases_path=Path(args.cases) if args.cases else None,
            output_cases_path=Path(args.output_cases) if args.output_cases else None,
            max_age_days=args.max_age_days,
            default_source=args.source,
            include_runtime_bootstrap=args.bootstrap_runtime,
        )
    elif args.command == "route-selector-model":
        result = write_route_selector_model(out_root, max_reports=max(1, args.max_reports))
    elif args.command == "runtime-health":
        result = run_runtime_health(
            out_root,
            codex_plus_root=Path(args.codex_plus_root) if args.codex_plus_root else None,
            min_documents=max(0, args.min_documents),
            min_projects=max(0, args.min_projects),
            min_sessions=max(0, args.min_sessions),
            min_workflows=max(0, args.min_workflows),
            min_semantic_chunks=max(0, args.min_semantic_chunks),
        )
    elif args.command == "v1-acceptance":
        result = run_v1_acceptance(
            out_root,
            codex_plus_root=Path(args.codex_plus_root) if args.codex_plus_root else None,
            refresh_health=args.refresh_health,
            refresh_evidence=args.refresh_evidence,
            min_documents=max(0, args.min_documents),
            min_projects=max(0, args.min_projects),
            min_sessions=max(0, args.min_sessions),
            min_workflows=max(0, args.min_workflows),
            min_semantic_chunks=max(0, args.min_semantic_chunks),
            required_trend_days=max(1, args.required_trend_days),
            mcp_timeout_seconds=max(5, args.mcp_timeout_seconds),
            codex_plus_timeout_seconds=max(5, args.codex_plus_timeout_seconds),
            with_manager_feedback_smoke=args.with_manager_feedback_smoke,
        )
    elif args.command == "v1-followup":
        result = run_v1_followup(
            out_root,
            codex_plus_root=Path(args.codex_plus_root) if args.codex_plus_root else None,
            run_when_ready=args.run_when_ready,
            force=args.force,
            min_documents=max(0, args.min_documents),
            min_projects=max(0, args.min_projects),
            min_sessions=max(0, args.min_sessions),
            min_workflows=max(0, args.min_workflows),
            min_semantic_chunks=max(0, args.min_semantic_chunks),
            required_trend_days=max(1, args.required_trend_days),
            mcp_timeout_seconds=max(5, args.mcp_timeout_seconds),
            codex_plus_timeout_seconds=max(5, args.codex_plus_timeout_seconds),
            with_manager_feedback_smoke=args.with_manager_feedback_smoke,
        )
    elif args.command == "v1-refresh":
        result = run_v1_refresh(
            out_root,
            codex_plus_root=Path(args.codex_plus_root) if args.codex_plus_root else None,
            force=args.force,
            refresh_semantic_evidence=not args.no_refresh_semantic_evidence,
            refresh_mcp_smoke=not args.no_refresh_mcp_smoke,
            refresh_runtime_health=not args.no_refresh_runtime_health,
            min_documents=max(0, args.min_documents),
            min_projects=max(0, args.min_projects),
            min_sessions=max(0, args.min_sessions),
            min_workflows=max(0, args.min_workflows),
            min_semantic_chunks=max(0, args.min_semantic_chunks),
            required_trend_days=max(1, args.required_trend_days),
            mcp_timeout_seconds=max(5, args.mcp_timeout_seconds),
            codex_plus_timeout_seconds=max(5, args.codex_plus_timeout_seconds),
            wait_for_semantic_evidence=args.wait_for_semantic_evidence,
            semantic_wait_timeout_seconds=max(0, args.semantic_wait_timeout_seconds),
            semantic_wait_poll_seconds=max(1, args.semantic_wait_poll_seconds),
            with_manager_feedback_smoke=args.with_manager_feedback_smoke,
        )
    elif args.command == "v1-stage-status":
        result = run_v1_stage_status(
            out_root,
            codex_plus_root=Path(args.codex_plus_root) if args.codex_plus_root else None,
        )
    elif args.command == "codex-plus-smoke":
        result = run_codex_plus_smoke(
            out_root,
            codex_plus_root=Path(args.codex_plus_root) if args.codex_plus_root else None,
            timeout_seconds=max(5, args.timeout_seconds),
            run_panel_status=True,
            run_manager_feedback=args.with_manager_feedback,
            run_runtime=args.with_runtime,
        )
    elif args.command == "semantic-readiness":
        result = run_semantic_readiness(
            out_root,
            min_semantic_chunks=max(0, args.min_semantic_chunks),
            required_trend_days=max(1, args.required_trend_days),
            label=args.label,
            launch_agents_dir=Path(args.launch_agents_dir) if args.launch_agents_dir else None,
            include_launchctl=args.with_launchctl,
        )
    elif args.command == "reproducibility-snapshot":
        roots = [out_root]
        if args.codex_plus_root:
            roots.append(Path(args.codex_plus_root))
        result = run_reproducibility_snapshot(out_root, roots=roots)
    elif args.command == "semantic-status":
        result = semantic_status()
    elif args.command == "semantic-refresh":
        result = run_semantic_refresh(
            out_root,
            source=args.source,
            budget=args.budget,
            backend=args.backend,
            text_chars=args.text_chars,
        )
    elif args.command == "semantic-maintain":
        result = run_semantic_maintenance(
            out_root,
            source=args.source,
            budget=args.budget,
            backend=args.backend,
            text_chars=args.text_chars,
            max_jobs=args.max_jobs,
            min_interval_minutes=args.min_interval_minutes,
        )
    elif args.command == "semantic-index-status":
        result = semantic_index_status(out_root)
    elif args.command == "semantic-benchmark":
        result = run_embedding_benchmark(
            out_root,
            source=args.source,
            queries=args.query,
            limit=args.limit,
        )
    elif args.command == "semantic-ann-prune":
        result = run_semantic_ann_prune(
            out_root,
            max_entries=args.max_entries,
            max_bytes=args.max_bytes,
            dry_run=args.dry_run,
        )
    elif args.command == "semantic-launchd":
        result = run_semantic_launchd(
            out_root,
            action=args.launchd_action,
            label=args.label,
            interval_minutes=args.interval_minutes,
            source=args.source,
            budget=args.budget,
            max_jobs=args.max_jobs,
            min_interval_minutes=args.min_interval_minutes,
            ann_max_entries=args.ann_max_entries,
            ann_max_bytes=args.ann_max_bytes,
            agent_context_bin=args.agent_context_bin,
            launch_agents_dir=Path(args.launch_agents_dir) if args.launch_agents_dir else None,
        )
    elif args.command == "semantic-launchd-status":
        result = semantic_launchd_status(
            out_root,
            label=args.label,
            launch_agents_dir=Path(args.launch_agents_dir) if args.launch_agents_dir else None,
            tail_lines=args.tail_lines,
            include_launchctl=args.with_launchctl,
        )
    elif args.command == "semantic-launchd-monitor":
        result = run_semantic_launchd_monitor(
            out_root,
            label=args.label,
            launch_agents_dir=Path(args.launch_agents_dir) if args.launch_agents_dir else None,
            tail_lines=args.tail_lines,
            with_launchctl=args.with_launchctl,
            max_history=args.max_history,
        )
    elif args.command == "semantic-launchd-wait":
        result = wait_for_semantic_launchd_run(
            out_root,
            label=args.label,
            launch_agents_dir=Path(args.launch_agents_dir) if args.launch_agents_dir else None,
            tail_lines=args.tail_lines,
            with_launchctl=args.with_launchctl,
            max_history=args.max_history,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
    elif args.command == "semantic-launchd-audit":
        result = run_semantic_launchd_audit(
            out_root,
            max_history=args.max_history,
            min_snapshots=args.min_snapshots,
            consecutive_unhealthy_threshold=args.consecutive_unhealthy_threshold,
            max_snapshot_age_seconds=args.max_snapshot_age_seconds,
            notify=args.notify,
            notify_on=args.notify_on,
        )
    elif args.command == "semantic-launchd-recover":
        result = run_semantic_launchd_recover(
            out_root,
            apply=args.apply,
            verify_after_apply=args.verify_after_apply,
            label=args.label,
            launch_agents_dir=Path(args.launch_agents_dir) if args.launch_agents_dir else None,
            max_history=args.max_history,
            agent_context_bin=args.agent_context_bin,
            interval_minutes=args.interval_minutes,
            source=args.source,
            budget=args.budget,
            max_jobs=args.max_jobs,
            min_interval_minutes=args.min_interval_minutes,
            ann_max_entries=args.ann_max_entries,
            ann_max_bytes=args.ann_max_bytes,
        )
    elif args.command == "semantic-launchd-trend":
        result = run_semantic_launchd_trend(
            out_root,
            max_history=args.max_history,
            min_days=args.min_days,
        )
    elif args.command == "access-policy":
        has_policy_update = any(
            [
                args.allow_provider,
                args.remove_allow_provider,
                args.deny_provider,
                args.remove_deny_provider,
                args.deny_path,
                args.remove_deny_path,
                args.require_consent_provider,
                args.remove_require_consent_provider,
                args.require_consent_path,
                args.remove_require_consent_path,
                args.audit_max_bytes is not None,
                args.audit_max_rotated_files is not None,
            ]
        )
        if args.write_default:
            result = write_default_access_policy(out_root, overwrite=args.overwrite)
            if has_policy_update:
                result = update_access_policy(
                    out_root,
                    allow_providers=args.allow_provider,
                    remove_allow_providers=args.remove_allow_provider,
                    deny_providers=args.deny_provider,
                    remove_deny_providers=args.remove_deny_provider,
                    deny_path_patterns=args.deny_path,
                    remove_deny_path_patterns=args.remove_deny_path,
                    require_consent_providers=args.require_consent_provider,
                    remove_require_consent_providers=args.remove_require_consent_provider,
                    require_consent_path_patterns=args.require_consent_path,
                    remove_require_consent_path_patterns=args.remove_require_consent_path,
                    audit_max_bytes=args.audit_max_bytes,
                    audit_max_rotated_files=args.audit_max_rotated_files,
                )
        elif has_policy_update:
            result = update_access_policy(
                out_root,
                allow_providers=args.allow_provider,
                remove_allow_providers=args.remove_allow_provider,
                deny_providers=args.deny_provider,
                remove_deny_providers=args.remove_deny_provider,
                deny_path_patterns=args.deny_path,
                remove_deny_path_patterns=args.remove_deny_path,
                require_consent_providers=args.require_consent_provider,
                remove_require_consent_providers=args.remove_require_consent_provider,
                require_consent_path_patterns=args.require_consent_path,
                remove_require_consent_path_patterns=args.remove_require_consent_path,
                audit_max_bytes=args.audit_max_bytes,
                audit_max_rotated_files=args.audit_max_rotated_files,
            )
        else:
            result = {"policy": load_access_policy(out_root)}
    elif args.command == "access-consent":
        result = mcp_grant_access_consent(args.identifier, reason=args.reason, out_root=str(out_root))
    elif args.command == "access-audit":
        result = read_access_audit(out_root, limit=args.limit)
    elif args.command == "mcp-live-smoke":
        result = run_mcp_live_smoke(
            out_root,
            codex_plus_root=Path(args.codex_plus_root) if args.codex_plus_root else None,
            timeout_seconds=max(5, args.timeout_seconds),
            with_manager_feedback_smoke=args.with_manager_feedback_smoke,
        )
    elif args.command == "mcp":
        run_mcp_server(str(out_root))
        return 0
    else:
        parser.error(f"unknown command: {args.command}")

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def regenerate_report(scope: Path, out_root: Path) -> dict:
    paths = IngestPaths.from_root(out_root)
    documents = read_jsonl(paths.documents_jsonl)
    chunks = read_jsonl(paths.chunks_jsonl)
    failures = read_jsonl(paths.failures_jsonl)
    write_report(paths.report_md, scope.expanduser().resolve(), documents, chunks, failures)
    return {
        "scope": str(scope.expanduser().resolve()),
        "documents": len(documents),
        "chunks": len(chunks),
        "failures": len(failures),
        "report": str(paths.report_md),
    }
