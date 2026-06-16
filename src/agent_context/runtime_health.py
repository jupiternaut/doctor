from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .codex_plus_smoke import latest_codex_plus_smoke_status
from .feedback_replay_trend import feedback_replay_trend_status
from .io import ensure_dir, read_jsonl, write_text
from .launchd import (
    DEFAULT_LAUNCHD_LABEL,
    latest_semantic_launchd_audit,
    latest_semantic_launchd_monitor,
    latest_semantic_launchd_trend,
    semantic_launchd_status,
)
from .mcp_live_smoke import latest_mcp_live_smoke_status
from .panel import latest_pack_paths
from .reproducibility import latest_reproducibility_snapshot_status
from .semantic_index import semantic_index_status


RUNTIME_HEALTH_VERSION = "0.1"
SEMANTIC_READINESS_VERSION = "0.1"
DEFAULT_CODEX_PLUS_ROOT = Path("/Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3")


def run_runtime_health(
    out_root: Path,
    *,
    codex_plus_root: Path | None = None,
    min_documents: int = 1,
    min_projects: int = 1,
    min_sessions: int = 1,
    min_workflows: int = 1,
    min_semantic_chunks: int = 16,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    started_at = datetime.now().astimezone()
    checks = [
        downloads_check(out_root, min_documents=max(0, min_documents)),
        providers_check(
            out_root,
            min_projects=max(0, min_projects),
            min_sessions=max(0, min_sessions),
            min_workflows=max(0, min_workflows),
        ),
        cold_indexes_check(out_root),
        semantic_runtime_check(out_root, min_semantic_chunks=max(0, min_semantic_chunks)),
        hot_pack_check(out_root),
        feedback_runtime_check(out_root),
        safety_check(out_root),
        mcp_surface_check(out_root),
        codex_plus_check(out_root, codex_plus_root),
        worktree_reproducibility_check(out_root, codex_plus_root),
    ]
    acceptance = acceptance_matrix(checks)
    summary = summarize_checks(checks)
    report_id = started_at.strftime("%Y%m%d%H%M%S%f")
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"runtime-health-{report_id}.json"
    md_path = reports_dir / f"runtime-health-{report_id}.md"
    latest_json_path = reports_dir / "runtime-health-latest.json"
    latest_md_path = reports_dir / "runtime-health-latest.md"
    report = {
        "runtime_health_version": RUNTIME_HEALTH_VERSION,
        "created_at": started_at.isoformat(),
        "out_root": str(out_root),
        "codex_plus_root": str(normalized_codex_plus_root(codex_plus_root) or ""),
        "status": summary["status"],
        "acceptance_ready": summary["status"] == "ok" and all(item["status"] == "ok" for item in acceptance),
        "summary": summary,
        "acceptance_matrix": acceptance,
        "checks": checks,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, text)
    write_text(latest_json_path, text)
    markdown = render_runtime_health_markdown(report)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return report


def run_semantic_readiness(
    out_root: Path,
    *,
    min_semantic_chunks: int = 16,
    required_trend_days: int = 2,
    label: str = DEFAULT_LAUNCHD_LABEL,
    launch_agents_dir: Path | None = None,
    include_launchctl: bool = False,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    started_at = datetime.now().astimezone()
    semantic = semantic_index_status(out_root)
    launchd = semantic_launchd_status(
        out_root,
        label=label,
        launch_agents_dir=launch_agents_dir,
        include_launchctl=include_launchctl,
    )
    monitor = latest_semantic_launchd_monitor(out_root)
    audit = latest_semantic_launchd_audit(out_root)
    trend = latest_semantic_launchd_trend(out_root)
    readiness = semantic_readiness(
        semantic,
        launchd,
        monitor,
        audit,
        trend,
        min_semantic_chunks=max(0, min_semantic_chunks),
        required_trend_days=max(1, required_trend_days),
    )
    report_id = started_at.strftime("%Y%m%d%H%M%S%f")
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"semantic-readiness-{report_id}.json"
    md_path = reports_dir / f"semantic-readiness-{report_id}.md"
    latest_json_path = reports_dir / "semantic-readiness-latest.json"
    latest_md_path = reports_dir / "semantic-readiness-latest.md"
    report = {
        "semantic_readiness_version": SEMANTIC_READINESS_VERSION,
        "created_at": started_at.isoformat(),
        "out_root": str(out_root),
        "status": readiness["status"],
        "ready": readiness["ready"],
        "next_action": semantic_readiness_next_action(readiness) if not readiness["ready"] else "",
        "readiness": readiness,
        "evidence": {
            "semantic_index": semantic,
            "semantic_launchd": {
                "health": launchd.get("health") or "",
                "installed": launchd.get("installed") is True,
                "issues": launchd.get("issues") or [],
                "plist_path": launchd.get("plist_path") or "",
                "script_path": launchd.get("script_path") or "",
                "launchctl": launchd.get("launchctl") if isinstance(launchd.get("launchctl"), dict) else {"checked": False},
            },
            "monitor": monitor,
            "audit": audit,
            "trend": trend,
        },
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_semantic_readiness_markdown(report)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return report


def downloads_check(out_root: Path, *, min_documents: int) -> dict[str, Any]:
    documents = out_root / "manifests" / "documents.jsonl"
    chunks = out_root / "manifests" / "chunks.jsonl"
    failures = out_root / "manifests" / "failures.jsonl"
    extracted = out_root / "extracted"
    document_count = count_jsonl(documents)
    chunk_count = count_jsonl(chunks)
    failure_count = count_jsonl(failures)
    extracted_count = len(list(extracted.glob("*.md"))) if extracted.exists() else 0
    if document_count >= min_documents and chunk_count > 0:
        status = "ok"
        summary = f"{document_count} documents, {chunk_count} chunks, {failure_count} failures"
        next_action = ""
    elif document_count > 0:
        status = "warning"
        summary = f"{document_count} documents but no chunks"
        next_action = "Run agent-context build/index for the Downloads scope."
    else:
        status = "failed"
        summary = "Downloads manifests are missing or empty"
        next_action = "Run agent-context build --scope /Users/gengrf/Downloads --with-index."
    return check(
        "downloads_ingestion",
        "Downloads ingestion",
        status,
        summary,
        evidence={
            "documents_jsonl": str(documents),
            "chunks_jsonl": str(chunks),
            "failures_jsonl": str(failures),
            "extracted_dir": str(extracted),
            "documents": document_count,
            "chunks": chunk_count,
            "failures": failure_count,
            "extracted_markdown_files": extracted_count,
        },
        next_action=next_action,
    )


def providers_check(
    out_root: Path,
    *,
    min_projects: int,
    min_sessions: int,
    min_workflows: int,
) -> dict[str, Any]:
    projects = out_root / "manifests" / "projects.jsonl"
    sessions = out_root / "manifests" / "sessions.jsonl"
    workflows = out_root / "manifests" / "workflows.jsonl"
    counts = {
        "projects": count_jsonl(projects),
        "sessions": count_jsonl(sessions),
        "workflows": count_jsonl(workflows),
    }
    missing = []
    if counts["projects"] < min_projects:
        missing.append("projects")
    if counts["sessions"] < min_sessions:
        missing.append("sessions")
    if counts["workflows"] < min_workflows:
        missing.append("workflows")
    status = "ok" if not missing else "warning"
    return check(
        "provider_layer",
        "Provider layer",
        status,
        f"projects={counts['projects']}, sessions={counts['sessions']}, workflows={counts['workflows']}",
        evidence={
            "projects_jsonl": str(projects),
            "sessions_jsonl": str(sessions),
            "workflows_jsonl": str(workflows),
            **counts,
        },
        next_action="Run agent-context providers and the project/session indexers." if missing else "",
    )


def cold_indexes_check(out_root: Path) -> dict[str, Any]:
    indexes = {
        "downloads": out_root / "indexes" / "context.sqlite",
        "projects": out_root / "indexes" / "projects.sqlite",
        "sessions": out_root / "indexes" / "sessions.sqlite",
    }
    evidence = {name: sqlite_status(path) for name, path in indexes.items()}
    missing = [name for name, item in evidence.items() if not item["exists"]]
    empty = [
        name
        for name, item in evidence.items()
        if item["exists"] and max_table_count(item, ("documents", "chunks")) == 0
    ]
    if missing:
        status = "failed"
        summary = f"Missing indexes: {', '.join(missing)}"
        next_action = "Run agent-context index, index-projects, and index-sessions."
    elif empty:
        status = "warning"
        summary = f"Indexes exist but look empty: {', '.join(empty)}"
        next_action = "Rebuild empty indexes before trusting resolver quality."
    else:
        status = "ok"
        summary = ", ".join(f"{name}={max_table_count(item, ('chunks', 'documents'))} rows" for name, item in evidence.items())
        next_action = ""
    return check("cold_indexes", "Cold indexes", status, summary, evidence=evidence, next_action=next_action)


def semantic_runtime_check(out_root: Path, *, min_semantic_chunks: int) -> dict[str, Any]:
    semantic = semantic_index_status(out_root)
    launchd = semantic_launchd_status(out_root, include_launchctl=False)
    monitor = latest_semantic_launchd_monitor(out_root)
    audit = latest_semantic_launchd_audit(out_root)
    trend = latest_semantic_launchd_trend(out_root)
    readiness = semantic_readiness(
        semantic,
        launchd,
        monitor,
        audit,
        trend,
        min_semantic_chunks=min_semantic_chunks,
    )
    chunks = int(semantic.get("chunks") or 0)
    health = str(launchd.get("health") or "not_checked")
    audit_status = str(audit.get("status") or audit.get("summary", {}).get("status") or "")
    trend_status = str(trend.get("status") or trend.get("summary", {}).get("status") or "")
    if not semantic.get("exists"):
        status = "failed"
        summary = "semantic.sqlite is missing"
        next_action = "Run agent-context semantic-refresh or semantic-maintain."
    elif chunks < min_semantic_chunks:
        status = "warning"
        summary = f"semantic.sqlite has only {chunks} chunks"
        next_action = "Run semantic-maintain with a larger budget."
    elif health in {"ok", "degraded"} and audit_status not in {"alert"}:
        status = "ok" if health == "ok" and trend_status not in {"short_window"} else "warning"
        summary = f"semantic chunks={chunks}, launchd health={health}"
        if trend_status == "short_window":
            next_action = semantic_readiness_next_action(readiness)
        else:
            next_action = "" if status == "ok" else "Inspect semantic launchd monitor/audit reports."
    else:
        status = "warning"
        summary = f"semantic chunks={chunks}, launchd health={health}"
        next_action = "Install or recover the semantic LaunchAgent, then run semantic-launchd-monitor."
    return check(
        "semantic_background",
        "Semantic background index",
        status,
        summary,
        evidence={
            "semantic_index": semantic,
            "semantic_launchd": {
                "health": health,
                "installed": launchd.get("installed") is True,
                "plist_path": launchd.get("plist_path") or "",
                "script_path": launchd.get("script_path") or "",
            },
            "monitor": monitor,
            "audit": audit,
            "trend": trend,
            "readiness": readiness,
        },
        next_action=next_action,
    )


def semantic_readiness(
    semantic: dict[str, Any],
    launchd: dict[str, Any],
    monitor: dict[str, Any],
    audit: dict[str, Any],
    trend: dict[str, Any],
    *,
    min_semantic_chunks: int,
    required_trend_days: int = 2,
) -> dict[str, Any]:
    raw_trend = read_json(optional_path(str(trend.get("path") or "")) or Path(""))
    raw_metrics = raw_trend.get("metrics") if isinstance(raw_trend.get("metrics"), dict) else {}
    trend_summary = trend.get("summary") if isinstance(trend.get("summary"), dict) else {}
    monitor_summary = monitor.get("summary") if isinstance(monitor.get("summary"), dict) else {}
    audit_summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
    trend_metrics = {**trend_summary, **raw_metrics}

    chunks = int_value(semantic.get("chunks"))
    days_observed = int_value(trend_metrics.get("days_observed"))
    required_trend_days = max(1, int(required_trend_days))
    days_remaining = max(0, required_trend_days - days_observed)
    trend_status = str(trend.get("status") or trend_summary.get("status") or "")
    trend_confidence = str(trend.get("confidence") or trend_summary.get("confidence") or "")
    launchd_health = str(launchd.get("health") or "not_checked")
    audit_health = str(audit.get("health") or audit.get("status") or audit_summary.get("health") or audit_summary.get("status") or "")
    latest_monitor_health = str(monitor_summary.get("latest_health") or "")
    unhealthy_snapshots = int_value(trend_metrics.get("unhealthy_snapshots"))

    if not semantic.get("exists") or chunks < min_semantic_chunks:
        status = "attention_required"
        reason = "semantic_index_not_ready"
    elif launchd_health not in {"ok", "degraded"}:
        status = "attention_required"
        reason = "launchd_not_ready"
    elif audit_health == "alert" or latest_monitor_health not in {"", "ok"} or unhealthy_snapshots > 0:
        status = "attention_required"
        reason = "background_health_needs_attention"
    elif trend_status == "ok" and trend_confidence == "multi_day":
        status = "ready"
        reason = "multi_day_trend_ok"
    elif trend_status == "short_window" or trend_confidence == "short_window":
        status = "waiting_for_time"
        reason = "healthy_but_short_window"
    else:
        status = "attention_required"
        reason = "trend_missing_or_unknown"

    limitations = raw_trend.get("limitations") if isinstance(raw_trend.get("limitations"), list) else []
    next_monitor_due_at = str(monitor_summary.get("next_expected_run_after") or "")
    earliest_multi_day_after = semantic_multi_day_earliest_after(raw_trend)
    return {
        "status": status,
        "ready": status == "ready",
        "reason": reason,
        "semantic_chunks": chunks,
        "min_semantic_chunks": min_semantic_chunks,
        "launchd_health": launchd_health,
        "launchd_installed": launchd.get("installed") is True,
        "audit_health": audit_health,
        "latest_monitor_health": latest_monitor_health,
        "monitor_snapshots": int_value(monitor_summary.get("snapshots")),
        "monitor_unhealthy_snapshots": int_value(monitor_summary.get("unhealthy_snapshots")),
        "latest_runs": monitor_summary.get("latest_runs"),
        "next_monitor_due_at": next_monitor_due_at,
        "seconds_until_next_monitor": monitor_summary.get("seconds_until_next_expected_run"),
        "trend_status": trend_status,
        "trend_confidence": trend_confidence,
        "trend_snapshots": int_value(trend_metrics.get("snapshots")),
        "trend_days_observed": days_observed,
        "required_trend_days": required_trend_days,
        "trend_days_remaining": days_remaining,
        "trend_runs_delta": int_value(trend_metrics.get("runs_delta")),
        "trend_unhealthy_snapshots": unhealthy_snapshots,
        "earliest_multi_day_check_after": earliest_multi_day_after,
        "limitations": limitations,
    }


def semantic_readiness_next_action(readiness: dict[str, Any]) -> str:
    if readiness.get("status") == "waiting_for_time":
        earliest = str(readiness.get("earliest_multi_day_check_after") or "")
        next_due = str(readiness.get("next_monitor_due_at") or "")
        parts = [
            f"Need {readiness.get('trend_days_remaining', 0)} more observed day(s) for multi-day confidence.",
        ]
        if earliest:
            parts.append(f"Earliest new-day evidence after {earliest}.")
        if next_due:
            parts.append(f"Next monitor due {next_due}.")
        parts.append("Then run semantic-launchd-trend and runtime-health again.")
        return " ".join(parts)
    return "Inspect semantic launchd readiness evidence and recover background maintenance if needed."


def semantic_multi_day_earliest_after(raw_trend: dict[str, Any]) -> str:
    daily = raw_trend.get("daily") if isinstance(raw_trend.get("daily"), list) else []
    buckets = [str(item.get("bucket") or "") for item in daily if isinstance(item, dict)]
    if not buckets:
        return ""
    latest_day = max(buckets)
    try:
        latest_date = datetime.strptime(latest_day, "%Y-%m-%d").date()
    except ValueError:
        return ""
    next_day = latest_date + timedelta(days=1)
    tzinfo = datetime.now().astimezone().tzinfo
    return datetime(next_day.year, next_day.month, next_day.day, tzinfo=tzinfo).isoformat()


def int_value(value: Any) -> int:
    return value if isinstance(value, int) else 0


def hot_pack_check(out_root: Path) -> dict[str, Any]:
    latest = latest_pack_paths(out_root)
    context = optional_path(latest.get("context_md_path"))
    sources = optional_path(latest.get("sources_jsonl_path"))
    manifest = optional_path(latest.get("manifest_json_path"))
    preflight = optional_path(latest.get("codex_preflight_md_path"))
    source_count = count_jsonl(sources) if sources and sources.exists() else 0
    manifest_data = read_json(manifest) if manifest else {}
    source_scope = str(manifest_data.get("source_scope") or "")
    required = [context, sources, manifest]
    missing = [str(path) if path else "" for path in required if not path or not path.exists()]
    if missing:
        status = "failed"
        summary = "Latest hot pack is missing required files"
        next_action = "Run agent-context resolve or codex-preflight for a real task goal."
    elif source_count == 0:
        status = "warning"
        summary = "Latest hot pack has no sources"
        next_action = "Run resolver with a broader source scope or rebuild indexes."
    elif source_scope and source_scope != "all":
        status = "warning"
        summary = f"latest context pack has {source_count} sources but source_scope={source_scope}"
        next_action = "Run agent-context codex-preflight or resolve with --source-scope all for full v1 provider coverage evidence."
    else:
        status = "ok"
        summary = f"latest context pack has {source_count} sources"
        next_action = ""
    return check(
        "hot_context_pack",
        "Hot context pack",
        status,
        summary,
        evidence={
            "context_md": str(context) if context else "",
            "sources_jsonl": str(sources) if sources else "",
            "manifest_json": str(manifest) if manifest else "",
            "codex_preflight_md": str(preflight) if preflight else "",
            "sources": source_count,
            "source_scope": source_scope,
        },
        next_action=next_action,
    )


def feedback_runtime_check(out_root: Path) -> dict[str, Any]:
    model = out_root / "feedback" / "model.json"
    route_model = out_root / "feedback" / "route_selector_model.json"
    model_data = read_json(model)
    route_model_data = read_json(route_model)
    replay_supervision_cases = int(model_data.get("replay_supervision_cases") or 0) if model_data else 0
    route_cases_seen = int(route_model_data.get("cases_seen") or 0) if route_model_data else 0
    curated_eval_cases = count_jsonl(out_root / "feedback" / "retrieval_eval_cases.curated.jsonl")
    replay_trend = feedback_replay_trend_status(out_root)
    replay_health = str(replay_trend.get("health") or "not_checked")
    if not model.exists():
        status = "warning"
        summary = "feedback/model.json is missing"
        next_action = "Run agent-context feedback-model and feedback-replay."
    elif replay_health == "alert":
        status = "failed"
        summary = "Feedback replay trend is alert"
        next_action = "Inspect latest feedback replay regression report before trusting rerank."
    elif replay_supervision_cases < 3 or route_cases_seen < 3 or curated_eval_cases == 0:
        status = "warning"
        summary = (
            "feedback exists but sample coverage is narrow "
            f"(replay={replay_supervision_cases}, route_cases={route_cases_seen}, curated_eval={curated_eval_cases})"
        )
        next_action = "Add/curate more retrieval eval cases before claiming feedback generalizes."
    elif replay_health == "ok":
        status = "ok"
        summary = "feedback model and replay trend are available"
        next_action = ""
    else:
        status = "warning"
        summary = f"feedback replay trend health={replay_health}"
        next_action = "Add replay cases and run feedback-replay-trend."
    return check(
        "feedback_loop",
        "Feedback loop",
        status,
        summary,
        evidence={
            "feedback_model": str(model),
            "feedback_model_exists": model.exists(),
            "route_selector_model": str(route_model),
            "route_selector_model_exists": route_model.exists(),
            "replay_supervision_cases": replay_supervision_cases,
            "route_selector_cases_seen": route_cases_seen,
            "curated_eval_cases": curated_eval_cases,
            "replay_trend": replay_trend,
        },
        next_action=next_action,
    )


def safety_check(out_root: Path) -> dict[str, Any]:
    policy = out_root / "config" / "access_policy.json"
    audit = out_root / "reports" / "access_audit.jsonl"
    policy_data = read_json(policy)
    deny_patterns = policy_data.get("deny_path_patterns") or [] if policy_data else []
    if not policy.exists():
        status = "failed"
        summary = "access_policy.json is missing"
        next_action = "Run agent-context access-policy --write-default."
    elif not deny_patterns:
        status = "warning"
        summary = "access policy exists but has no deny path patterns"
        next_action = "Add sensitive path deny patterns before exposing read_source widely."
    else:
        status = "ok"
        summary = f"access policy has {len(deny_patterns)} deny path patterns"
        next_action = ""
    return check(
        "safety_permissions",
        "Safety and permissions",
        status,
        summary,
        evidence={
            "access_policy_json": str(policy),
            "access_policy_exists": policy.exists(),
            "deny_path_patterns": len(deny_patterns),
            "access_audit_jsonl": str(audit),
            "access_audit_exists": audit.exists(),
            "access_audit_events": count_jsonl(audit),
        },
        next_action=next_action,
    )


def mcp_surface_check(out_root: Path) -> dict[str, Any]:
    import agent_context.mcp_server as mcp_server

    required = [
        "mcp_resolve_context",
        "mcp_search_context",
        "mcp_read_source",
        "mcp_context_panel",
        "mcp_record_panel_feedback",
        "mcp_semantic_refresh",
        "mcp_semantic_maintain",
        "mcp_semantic_readiness",
        "mcp_feedback_replay",
        "mcp_feedback_replay_trend",
        "mcp_runtime_health",
        "mcp_v1_acceptance",
        "mcp_v1_followup",
        "mcp_v1_refresh",
        "mcp_codex_plus_smoke",
        "mcp_reproducibility_snapshot",
        "mcp_access_policy",
        "mcp_grant_access_consent",
    ]
    missing = [name for name in required if not hasattr(mcp_server, name)]
    live_smoke = latest_mcp_live_smoke_status(out_root)
    if missing:
        status = "failed"
        summary = "missing tools: " + ", ".join(missing)
        next_action = "Expose missing runtime tools through create_mcp_server."
    elif live_smoke.get("status") == "ok":
        status = "ok"
        summary = f"{len(required)} required tool functions are importable and live stdio smoke passed"
        next_action = ""
    else:
        status = "warning"
        summary = f"{len(required)} required tool functions are importable but live stdio smoke is {live_smoke.get('status')}"
        next_action = "Run agent-context mcp-live-smoke before claiming MCP client acceptance."
    return check(
        "mcp_surface",
        "MCP surface",
        status,
        summary,
        evidence={"required_functions": required, "missing_functions": missing, "live_smoke": live_smoke},
        next_action=next_action,
    )


def codex_plus_check(out_root: Path, codex_plus_root: Path | None) -> dict[str, Any]:
    root = normalized_codex_plus_root(codex_plus_root)
    if not root:
        return check(
            "codex_plus_integration",
            "Codex++ integration",
            "warning",
            "Codex++ repo was not found for runtime health inspection",
            evidence={"codex_plus_root": ""},
            next_action="Pass --codex-plus-root to verify default hook and Manager smoke scripts.",
        )
    required = [
        root / "scripts" / "smoke-agent-context-runtime.mjs",
        root / "scripts" / "smoke-agent-context-panel-status.mjs",
        root / "scripts" / "smoke-agent-context-manager-feedback-replay.mjs",
        root / "assets" / "inject" / "renderer-inject.js",
        root / "crates" / "codex-plus-core" / "src" / "agent_context.rs",
        root / "apps" / "codex-plus-manager" / "src" / "App.tsx",
    ]
    missing = [str(path) for path in required if not path.exists()]
    smoke = latest_codex_plus_smoke_status(out_root)
    if missing:
        status = "failed"
        summary = "Codex++ integration files are missing"
        next_action = "Restore or generate missing Codex++ integration files."
    elif smoke.get("status") == "ok":
        status = "ok"
        summary = "Codex++ default hook and Manager smoke passed"
        next_action = ""
    elif smoke.get("status") == "missing":
        status = "warning"
        summary = "Codex++ integration files are present but smoke has not run"
        next_action = "Run agent-context codex-plus-smoke or v1-acceptance --refresh-evidence."
    else:
        status = "failed"
        summary = f"Codex++ integration smoke status={smoke.get('status')}"
        next_action = "Inspect reports/codex-plus-smoke-latest.*."
    return check(
        "codex_plus_integration",
        "Codex++ integration",
        status,
        summary,
        evidence={
            "codex_plus_root": str(root),
            "required_files": [str(path) for path in required],
            "missing_files": missing,
            "smoke": smoke,
        },
        next_action=next_action,
    )


def worktree_reproducibility_check(out_root: Path, codex_plus_root: Path | None) -> dict[str, Any]:
    roots = [out_root]
    codex_root = normalized_codex_plus_root(codex_plus_root)
    if codex_root:
        roots.append(codex_root)
    evidence = {str(root): git_status(root) for root in roots}
    dirty = [root for root, status in evidence.items() if status.get("is_repo") and status.get("dirty")]
    missing_repo = [root for root, status in evidence.items() if not status.get("is_repo")]
    snapshot = latest_reproducibility_snapshot_status(out_root, roots)
    if dirty and snapshot.get("status") == "ok":
        status = "ok"
        summary = f"dirty worktrees covered by reproducibility snapshot: {len(dirty)}"
        next_action = ""
    elif dirty:
        status = "warning"
        summary = f"dirty worktrees: {len(dirty)}"
        next_action = "Commit or snapshot the intended v1 changes before using this as release acceptance evidence."
    elif missing_repo:
        status = "warning"
        summary = f"non-git roots: {len(missing_repo)}"
        next_action = "Record a reproducible artifact version for non-git roots."
    else:
        status = "ok"
        summary = "inspected git worktrees are clean"
        next_action = ""
    return check(
        "reproducibility",
        "Reproducibility",
        status,
        summary,
        evidence={"worktrees": evidence, "snapshot": snapshot},
        next_action=next_action,
    )


def normalized_codex_plus_root(codex_plus_root: Path | None) -> Path | None:
    if codex_plus_root:
        root = codex_plus_root.expanduser().resolve()
        return root if root.exists() else None
    return DEFAULT_CODEX_PLUS_ROOT if DEFAULT_CODEX_PLUS_ROOT.exists() else None


def acceptance_matrix(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in checks}
    requirements = [
        (
            "v1.1",
            "Codex++ 面板可以生成 context pack",
            ["codex_plus_integration", "hot_context_pack"],
            "Codex++ integration files plus latest hot context pack evidence.",
        ),
        (
            "v1.2",
            "Resolver 默认覆盖 Downloads/Git/session/workflow providers",
            ["provider_layer", "hot_context_pack"],
            "Provider manifests and latest pack source_scope/all evidence.",
        ),
        (
            "v1.3",
            "冷索引有关键词召回和后台语义召回",
            ["cold_indexes", "semantic_background"],
            "SQLite indexes plus semantic.sqlite/launchd evidence.",
        ),
        (
            "v1.4",
            "MCP 暴露核心 runtime tools",
            ["mcp_surface"],
            "Importable MCP tool functions for resolve/read/panel/feedback/semantic.",
        ),
        (
            "v1.5",
            "用户反馈影响后续排序并可 replay",
            ["feedback_loop"],
            "feedback/model.json, route selector model, replay trend and sample coverage.",
        ),
        (
            "v1.6",
            "文件读取只读且权限边界清楚",
            ["safety_permissions"],
            "access_policy.json and access audit metadata evidence.",
        ),
        (
            "v1.7",
            "热包包含路径、摘要、引用、限制和下一步",
            ["hot_context_pack"],
            "Latest context.md/sources.jsonl/manifest.json existence and non-empty sources.",
        ),
    ]
    matrix = []
    for req_id, title, check_ids, evidence in requirements:
        statuses = [str((by_id.get(check_id) or {}).get("status") or "failed") for check_id in check_ids]
        if "failed" in statuses:
            status = "failed"
        elif "warning" in statuses:
            status = "warning"
        else:
            status = "ok"
        matrix.append(
            {
                "id": req_id,
                "title": title,
                "status": status,
                "checks": check_ids,
                "evidence": evidence,
            }
        )
    return matrix


def check(
    check_id: str,
    title: str,
    status: str,
    summary: str,
    *,
    evidence: dict[str, Any],
    next_action: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "summary": summary,
        "evidence": evidence,
        "next_action": next_action,
    }


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"ok": 0, "warning": 0, "failed": 0}
    for item in checks:
        status = str(item.get("status") or "warning")
        counts[status if status in counts else "warning"] += 1
    if counts["failed"]:
        status = "failed"
    elif counts["warning"]:
        status = "warning"
    else:
        status = "ok"
    return {
        "status": status,
        "ok": counts["ok"],
        "warning": counts["warning"],
        "failed": counts["failed"],
        "checks_total": len(checks),
        "blocking_checks": [item["id"] for item in checks if item.get("status") == "failed"],
        "warning_checks": [item["id"] for item in checks if item.get("status") == "warning"],
    }


def render_runtime_health_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agent Context Runtime Health",
        "",
        f"- status: `{report['status']}`",
        f"- acceptance_ready: `{str(report['acceptance_ready']).lower()}`",
        f"- created_at: `{report['created_at']}`",
        f"- out_root: `{report['out_root']}`",
        f"- codex_plus_root: `{report.get('codex_plus_root') or ''}`",
        "",
        "## Acceptance Matrix",
        "",
        "| Requirement | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for item in report.get("acceptance_matrix") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md(f"{item['id']} {item['title']}"),
                    f"`{escape_md(str(item['status']))}`",
                    escape_md(str(item.get("evidence") or "")),
                ]
            )
            + " |"
        )
    lines.extend([
        "",
        "## Summary",
        "",
        f"- ok: {report['summary']['ok']}",
        f"- warning: {report['summary']['warning']}",
        f"- failed: {report['summary']['failed']}",
        "",
        "## Checks",
        "",
        "| Check | Status | Evidence | Next action |",
        "| --- | --- | --- | --- |",
    ])
    for item in report["checks"]:
        evidence = primary_evidence(item.get("evidence") or {})
        next_action = str(item.get("next_action") or "")
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md(str(item.get("title") or item.get("id") or "")),
                    f"`{escape_md(str(item.get('status') or ''))}`",
                    escape_md(evidence or str(item.get("summary") or "")),
                    escape_md(next_action),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Notes", ""])
    if report["summary"]["blocking_checks"]:
        lines.append("- Blocking checks must be fixed before claiming v1 completion.")
    if report["summary"]["warning_checks"]:
        lines.append("- Warning checks are not fatal for local use, but they are not proof of full v1 completion.")
    if not report["summary"]["blocking_checks"] and not report["summary"]["warning_checks"]:
        lines.append("- All runtime health checks passed for this snapshot.")
    lines.append("")
    return "\n".join(lines)


def render_semantic_readiness_markdown(report: dict[str, Any]) -> str:
    readiness = report.get("readiness") if isinstance(report.get("readiness"), dict) else {}
    limitations = readiness.get("limitations") if isinstance(readiness.get("limitations"), list) else []
    limitation_lines = [f"- {item}" for item in limitations] or ["- none"]
    return "\n".join(
        [
            "# Semantic Readiness",
            "",
            f"- status: `{report.get('status', '')}`",
            f"- ready: `{str(report.get('ready', False)).lower()}`",
            f"- reason: `{readiness.get('reason', '')}`",
            f"- semantic_chunks: `{readiness.get('semantic_chunks', 0)}`",
            f"- launchd_health: `{readiness.get('launchd_health', '')}`",
            f"- latest_monitor_health: `{readiness.get('latest_monitor_health', '')}`",
            f"- monitor_snapshots: `{readiness.get('monitor_snapshots', 0)}`",
            f"- trend_status: `{readiness.get('trend_status', '')}`",
            f"- trend_days_observed: `{readiness.get('trend_days_observed', 0)}`",
            f"- trend_days_remaining: `{readiness.get('trend_days_remaining', 0)}`",
            f"- next_monitor_due_at: `{readiness.get('next_monitor_due_at', '')}`",
            f"- earliest_multi_day_check_after: `{readiness.get('earliest_multi_day_check_after', '')}`",
            "",
            "## Next Action",
            "",
            report.get("next_action") or "No action required.",
            "",
            "## Limitations",
            "",
            *limitation_lines,
            "",
        ]
    )


def primary_evidence(evidence: dict[str, Any]) -> str:
    for key in (
        "context_md",
        "index_path",
        "feedback_model",
        "access_policy_json",
        "projects_jsonl",
        "documents_jsonl",
        "codex_plus_root",
    ):
        value = evidence.get(key)
        if value:
            return str(value)
    if "required_functions" in evidence:
        return f"{len(evidence['required_functions'])} MCP functions"
    if "semantic_index" in evidence:
        return str((evidence["semantic_index"] or {}).get("index_path") or "")
    return ""


def sqlite_status(path: Path) -> dict[str, Any]:
    status: dict[str, Any] = {"path": str(path), "exists": path.exists(), "tables": {}}
    if not path.exists():
        return status
    try:
        conn = sqlite3.connect(path)
        try:
            names = [
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                if not str(row[0]).startswith("sqlite_")
            ]
            for name in names:
                try:
                    status["tables"][name] = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                except sqlite3.DatabaseError as exc:
                    status["tables"][name] = f"error: {exc}"
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        status["error"] = str(exc)
    return status


def max_table_count(status: dict[str, Any], names: tuple[str, ...]) -> int:
    tables = status.get("tables") or {}
    values = [tables.get(name) for name in names]
    ints = [int(value) for value in values if isinstance(value, int)]
    return max(ints) if ints else 0


def count_jsonl(path: Path) -> int:
    try:
        return len(read_jsonl(path))
    except (OSError, json.JSONDecodeError):
        return 0


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def git_status(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {"path": str(root), "exists": False, "is_repo": False, "dirty": False, "short": ""}
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--short", "--branch"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return {
            "path": str(root),
            "exists": True,
            "is_repo": False,
            "dirty": False,
            "short": (result.stderr or result.stdout).strip(),
        }
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    dirty_lines = [line for line in lines if not line.startswith("##")]
    return {
        "path": str(root),
        "exists": True,
        "is_repo": True,
        "dirty": bool(dirty_lines),
        "branch": next((line for line in lines if line.startswith("##")), ""),
        "dirty_count": len(dirty_lines),
        "short": "\n".join(lines[:40]),
    }


def optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
