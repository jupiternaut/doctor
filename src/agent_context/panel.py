from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .access_policy import read_access_audit
from .codex_hook import build_codex_preflight
from .feedback_model import write_feedback_model
from .feedback_replay_trend import feedback_replay_trend_status
from .io import ensure_dir, read_jsonl, write_text
from .launchd import (
    latest_semantic_launchd_audit,
    latest_semantic_launchd_monitor,
    latest_semantic_launchd_recover,
    latest_semantic_launchd_trend,
    semantic_launchd_status,
)
from .semantic import semantic_status
from .semantic_index import semantic_index_status


PANEL_VERSION = "0.1"
PANEL_DIRNAME = "panel"
PANEL_FEEDBACK_VERSION = "0.1"


def build_context_panel(
    out_root: Path,
    *,
    goal: str | None = None,
    source_scope: str = "all",
    mode: str = "fast",
    limit: int = 12,
    auto_context: bool = True,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    preflight = None
    if goal:
        preflight = build_codex_preflight(
            out_root,
            goal,
            source_scope=source_scope,
            limit=limit,
            auto_context=auto_context,
            mode=mode,
        )

    status = panel_status(
        out_root,
        goal=goal,
        source_scope=source_scope,
        mode=mode,
        auto_context=auto_context,
        preflight=preflight,
    )
    panel_dir = ensure_dir(out_root / PANEL_DIRNAME)
    status_path = panel_dir / "status.json"
    html_path = panel_dir / "context_panel.html"
    write_text(status_path, json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(html_path, render_panel_html(status, status_path))
    return {
        "panel_version": PANEL_VERSION,
        "status_json_path": str(status_path),
        "html_path": str(html_path),
        "goal": goal,
        "auto_context": auto_context,
        "mode": mode,
        "source_scope": source_scope,
        "last_generated_pack": status["last_generated_pack"],
        "last_sources_jsonl": status["last_sources_jsonl"],
        "feedback_jsonl_path": status["feedback"]["panel_feedback_jsonl_path"],
    }


def panel_status(
    out_root: Path,
    *,
    goal: str | None = None,
    source_scope: str = "all",
    mode: str = "fast",
    auto_context: bool = True,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    latest_pack = latest_pack_paths(out_root)
    feedback_model = write_feedback_model(out_root)
    feedback_replay = feedback_replay_trend_status(out_root)
    access_audit = read_access_audit(out_root, limit=20)
    semantic_launchd = semantic_launchd_status(out_root, include_launchctl=True)
    semantic_launchd["monitor"] = latest_semantic_launchd_monitor(out_root)
    semantic_launchd["audit"] = latest_semantic_launchd_audit(out_root)
    semantic_launchd["recovery"] = latest_semantic_launchd_recover(out_root)
    semantic_launchd["trend"] = latest_semantic_launchd_trend(out_root)
    semantic_readiness = latest_semantic_readiness(out_root)
    v1_acceptance = latest_v1_acceptance(out_root)
    runtime_vm = latest_runtime_vm_acceptance(out_root)
    refresh_v1_stage_status_for_panel(out_root)
    v1_stage_status = latest_v1_stage_status(out_root)
    return {
        "panel_version": PANEL_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "out_root": str(out_root),
        "auto_context": auto_context,
        "scope": source_scope,
        "mode": mode,
        "goal": goal,
        "preflight": preflight_summary(preflight),
        "last_generated_pack": latest_pack.get("context_md_path"),
        "last_sources_jsonl": latest_pack.get("sources_jsonl_path"),
        "last_manifest_json": latest_pack.get("manifest_json_path"),
        "last_resolution_plan_json": latest_pack.get("resolution_plan_json_path"),
        "last_codex_preflight_md": latest_pack.get("codex_preflight_md_path"),
        "indexes": {
            "downloads": index_status(out_root / "indexes" / "context.sqlite"),
            "projects": index_status(out_root / "indexes" / "projects.sqlite"),
            "semantic": index_status(out_root / "indexes" / "semantic.sqlite"),
        },
        "semantic": semantic_status(),
        "semantic_index": semantic_index_status(out_root),
        "semantic_launchd": semantic_launchd,
        "semantic_readiness": semantic_readiness,
        "v1_acceptance": v1_acceptance,
        "runtime_vm": runtime_vm,
        "v1_stage_status": v1_stage_status,
        "feedback": {
            "panel_feedback_jsonl_path": str(out_root / "feedback" / "panel_feedback.jsonl"),
            "mcp_feedback_count": len(read_jsonl(out_root / "feedback" / "mcp_feedback.jsonl")),
            "arena_feedback_count": len(read_jsonl(out_root / "feedback" / "arena_feedback.jsonl")),
            "panel_feedback_count": len(read_jsonl(out_root / "feedback" / "panel_feedback.jsonl")),
            "model_path": feedback_model["feedback_model_path"],
            "source_score_count": len(feedback_model.get("source_scores") or {}),
            "replay_trend": feedback_replay,
        },
        "access_audit": {
            "audit_path": access_audit["audit_path"],
            "events_total": access_audit["events_total"],
            "recent_events": access_audit["events"],
            "summary": summarize_access_events(access_audit["events"]),
        },
    }


def record_panel_feedback(
    out_root: Path,
    *,
    source: str,
    rating: str,
    reason: str = "",
    status_path: str | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    path = out_root / "feedback" / "panel_feedback.jsonl"
    event = {
        "panel_feedback_version": PANEL_FEEDBACK_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "source": source,
        "selected_source": source,
        "rating": rating,
        "reason": reason,
        "status_path": status_path or str(out_root / PANEL_DIRNAME / "status.json"),
    }
    append_jsonl(path, event)
    model = write_feedback_model(out_root)
    return {
        "panel_feedback_version": PANEL_FEEDBACK_VERSION,
        "feedback_path": str(path),
        "record": event,
        "feedback_model_path": model["feedback_model_path"],
        "source_score_count": len(model.get("source_scores") or {}),
    }


def latest_pack_paths(out_root: Path) -> dict[str, str | None]:
    packs = out_root / "packs"
    if not packs.exists():
        return {}
    context_files = sorted(packs.glob("*/context.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not context_files:
        return {}
    context_path = context_files[0]
    pack_dir = context_path.parent
    return {
        "context_md_path": str(context_path),
        "sources_jsonl_path": existing_path(pack_dir / "sources.jsonl"),
        "manifest_json_path": existing_path(pack_dir / "manifest.json"),
        "resolution_plan_json_path": existing_path(pack_dir / "resolution_plan.json"),
        "codex_preflight_md_path": existing_path(pack_dir / "codex_preflight.md"),
    }


def existing_path(path: Path) -> str | None:
    return str(path) if path.exists() else None


def preflight_summary(preflight: dict[str, Any] | None) -> dict[str, Any] | None:
    if not preflight:
        return None
    return {
        "status": preflight.get("status"),
        "task_id": preflight.get("task_id"),
        "intent": preflight.get("intent"),
        "sources_included": preflight.get("sources_included"),
        "preflight_markdown_path": preflight.get("preflight_markdown_path"),
    }


def index_status(path: Path) -> dict[str, Any]:
    status: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return status
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            meta = {str(row["key"]): str(row["value"]) for row in conn.execute("SELECT key, value FROM meta")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        status["error"] = str(exc)
        return status
    status["meta"] = meta
    return status


def summarize_access_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: dict[str, int] = {}
    actions: dict[str, int] = {}
    for event in events:
        decision = str(event.get("decision") or "")
        action = str(event.get("action") or "")
        decisions[decision] = decisions.get(decision, 0) + 1
        actions[action] = actions.get(action, 0) + 1
    last_denied = next((event for event in reversed(events) if event.get("decision") == "denied"), None)
    return {
        "recent_count": len(events),
        "decisions": decisions,
        "actions": actions,
        "last_denied": access_event_summary(last_denied) if last_denied else None,
    }


def access_event_summary(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {}
    return {
        "created_at": event.get("created_at", ""),
        "action": event.get("action", ""),
        "decision": event.get("decision", ""),
        "identifier": event.get("identifier", ""),
        "provider": event.get("provider", ""),
        "path": event.get("path", ""),
        "reason": event.get("reason", ""),
    }


def render_panel_html(status: dict[str, Any], status_path: Path) -> str:
    rows = [
        ("Auto Context", str(status["auto_context"])),
        ("Scope", status["scope"]),
        ("Mode", status["mode"]),
        ("Last Pack", status.get("last_generated_pack") or ""),
        ("Sources", status.get("last_sources_jsonl") or ""),
        ("Manifest", status.get("last_manifest_json") or ""),
        ("Feedback", status["feedback"]["panel_feedback_jsonl_path"]),
        ("Feedback Replay Health", (status["feedback"].get("replay_trend") or {}).get("health") or ""),
        (
            "Feedback Replay Top1",
            (status["feedback"].get("replay_trend") or {}).get("summary", {}).get("latest_expected_top1_rate", ""),
        ),
        (
            "Feedback Replay Regressions",
            (status["feedback"].get("replay_trend") or {}).get("summary", {}).get("trend_rank_regressions", ""),
        ),
        ("Access Audit", status["access_audit"]["audit_path"]),
        ("Semantic LaunchAgent", status["semantic_launchd"]["health"]),
        ("Semantic LaunchAgent Audit", (status["semantic_launchd"].get("audit") or {}).get("health") or ""),
        ("Semantic LaunchAgent Recovery", (status["semantic_launchd"].get("recovery") or {}).get("status") or ""),
        ("Semantic LaunchAgent Trend", (status["semantic_launchd"].get("trend") or {}).get("status") or ""),
        ("Semantic Readiness", (status.get("semantic_readiness") or {}).get("status") or ""),
        ("Semantic Ready", (status.get("semantic_readiness") or {}).get("ready", "")),
        ("Semantic Next Action", (status.get("semantic_readiness") or {}).get("next_action") or ""),
        ("V1 Acceptance", (status.get("v1_acceptance") or {}).get("status") or ""),
        ("V1 Ready", (status.get("v1_acceptance") or {}).get("ready", "")),
        ("V1 Decision", (status.get("v1_acceptance") or {}).get("decision") or ""),
        ("V1 Acceptance Report", (status.get("v1_acceptance") or {}).get("latest_md_path") or ""),
        ("V1 Follow-Up Plan", (status.get("v1_acceptance") or {}).get("latest_followup_md_path") or ""),
        (
            "V1 Follow-Up Wait Reason",
            ((status.get("v1_acceptance") or {}).get("followup_check") or {}).get("wait_reason") or "",
        ),
        (
            "V1 Follow-Up Next Gate",
            ((status.get("v1_acceptance") or {}).get("followup_check") or {}).get("next_gate_at") or "",
        ),
        (
            "V1 Acceptance Wait Reason",
            ((status.get("v1_acceptance") or {}).get("followup_check") or {}).get("acceptance_wait_reason")
            or "",
        ),
        (
            "V1 Acceptance Gate",
            ((status.get("v1_acceptance") or {}).get("followup_check") or {}).get("acceptance_gate_at") or "",
        ),
        ("V1 Stage Status", (status.get("v1_stage_status") or {}).get("status") or ""),
        ("V1 Stage Ready", (status.get("v1_stage_status") or {}).get("ready", "")),
        ("V1 Stage Report", (status.get("v1_stage_status") or {}).get("latest_md_path") or ""),
        (
            "V1 Stage Counts",
            (status.get("v1_stage_status") or {}).get("summary", {}).get("status_counts", ""),
        ),
        (
            "V1 Stage Gates",
            (status.get("v1_stage_status") or {}).get("next_gates", {}),
        ),
        ("Runtime VM Status", (status.get("runtime_vm") or {}).get("status") or ""),
        ("Runtime VM Ready", (status.get("runtime_vm") or {}).get("ready", "")),
        ("Runtime VM Session", (status.get("runtime_vm") or {}).get("session_id") or ""),
        ("Runtime VM Review File", (status.get("runtime_vm") or {}).get("review_file") or ""),
        ("Runtime VM Agent Handoff", (status.get("runtime_vm") or {}).get("agent_handoff_md_path") or ""),
        ("Runtime VM Adapter Manifest", (status.get("runtime_vm") or {}).get("runtime_adapter_manifest_json_path") or ""),
        ("Runtime VM Artifact Index", (status.get("runtime_vm") or {}).get("execution_artifact_index_md_path") or ""),
        ("Runtime VM Report", (status.get("runtime_vm") or {}).get("latest_md_path") or ""),
        (
            "Runtime VM Next Commands",
            "\n".join((status.get("runtime_vm") or {}).get("next_commands") or []),
        ),
    ]
    row_html = "\n".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(value))}</td></tr>"
        for label, value in rows
    )
    access_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(event.get('created_at', '')))}</td>"
        f"<td>{html.escape(str(event.get('action', '')))}</td>"
        f"<td>{html.escape(str(event.get('decision', '')))}</td>"
        f"<td>{html.escape(str(event.get('identifier', '')))}</td>"
        f"<td>{html.escape(str(event.get('provider', '')))}</td>"
        f"<td>{html.escape(str(event.get('reason', '')))}</td>"
        "</tr>"
        for event in status["access_audit"]["recent_events"][-10:]
    )
    if not access_rows:
        access_rows = "<tr><td colspan=\"6\">No access audit events yet.</td></tr>"
    status_json = html.escape(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Agent Context Panel</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2937; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 1100px; }}
    th, td {{ border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ width: 180px; background: #f9fafb; }}
    pre {{ background: #111827; color: #f9fafb; padding: 16px; overflow: auto; max-height: 560px; }}
  </style>
</head>
<body>
  <h1>Agent Context Panel</h1>
  <table>{row_html}</table>
  <h2>Access Audit</h2>
  <table>
    <tr><th>Time</th><th>Action</th><th>Decision</th><th>Identifier</th><th>Provider</th><th>Reason</th></tr>
    {access_rows}
  </table>
  <p>Status JSON: <code>{html.escape(str(status_path))}</code></p>
  <h2>Raw Status</h2>
  <pre>{status_json}</pre>
</body>
</html>
"""


def latest_semantic_readiness(out_root: Path) -> dict[str, Any]:
    latest_json_path = out_root.expanduser().resolve() / "reports" / "semantic-readiness-latest.json"
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "status": "missing",
            "ready": False,
            "next_action": "Run agent-context semantic-readiness.",
            "summary": {},
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "status": "failed",
            "ready": False,
            "error": str(exc),
            "next_action": "Regenerate semantic-readiness.",
            "summary": {},
        }
    readiness = data.get("readiness") if isinstance(data.get("readiness"), dict) else {}
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "status": data.get("status", ""),
        "ready": data.get("ready") is True,
        "next_action": data.get("next_action", ""),
        "summary": {
            "reason": readiness.get("reason", ""),
            "semantic_chunks": readiness.get("semantic_chunks", 0),
            "launchd_health": readiness.get("launchd_health", ""),
            "latest_monitor_health": readiness.get("latest_monitor_health", ""),
            "trend_days_observed": readiness.get("trend_days_observed", 0),
            "trend_days_remaining": readiness.get("trend_days_remaining", 0),
            "monitor_snapshots": readiness.get("monitor_snapshots", 0),
            "next_monitor_due_at": readiness.get("next_monitor_due_at", ""),
            "earliest_multi_day_check_after": readiness.get("earliest_multi_day_check_after", ""),
        },
    }


def latest_v1_acceptance(out_root: Path) -> dict[str, Any]:
    root = out_root.expanduser().resolve()
    latest_json_path = root / "reports" / "v1-acceptance-latest.json"
    followup_check = latest_v1_followup_check(root)
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "status": "missing",
            "ready": False,
            "decision": "",
            "latest_md_path": "",
            "latest_followup_md_path": "",
            "latest_followup_json_path": "",
            "followup_plan": {},
            "followup_check": followup_check,
            "next_commands": [],
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "status": "failed",
            "ready": False,
            "decision": "",
            "latest_md_path": "",
            "latest_followup_md_path": "",
            "latest_followup_json_path": "",
            "followup_plan": {},
            "followup_check": followup_check,
            "error": str(exc),
            "next_commands": [],
        }
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "status": data.get("status", ""),
        "ready": data.get("ready") is True,
        "decision": data.get("decision", ""),
        "created_at": data.get("created_at", ""),
        "next_commands": data.get("next_commands") if isinstance(data.get("next_commands"), list) else [],
        "followup_plan": data.get("followup_plan") if isinstance(data.get("followup_plan"), dict) else {},
        "followup_check": followup_check,
        "latest_followup_md_path": data.get("latest_followup_md_path", ""),
        "latest_followup_json_path": data.get("latest_followup_json_path", ""),
    }


def latest_runtime_vm_acceptance(out_root: Path) -> dict[str, Any]:
    latest_json_path = out_root.expanduser().resolve() / "reports" / "runtime-vm-acceptance-latest.json"
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "status": "missing",
            "ready": False,
            "session_id": "",
            "latest_md_path": "",
            "review_file": "",
            "next_commands": [],
            "checks": [],
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "status": "failed",
            "ready": False,
            "session_id": "",
            "latest_md_path": "",
            "review_file": "",
            "next_commands": [],
            "checks": [],
            "error": str(exc),
        }
    session = data.get("session") if isinstance(data.get("session"), dict) else {}
    next_state = session.get("next") if isinstance(session.get("next"), dict) else {}
    files = session.get("files") if isinstance(session.get("files"), dict) else {}
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    missing_required = [
        check.get("id")
        for check in checks
        if isinstance(check, dict) and check.get("required_for_complete") and check.get("status") != "ok"
    ]
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "status": data.get("status", ""),
        "ready": data.get("ready") is True,
        "session_id": data.get("session_id", ""),
        "review_file": next_state.get("review_file", ""),
        "agent_handoff_md_path": files.get("agent_handoff_md_path", ""),
        "runtime_adapter_manifest_json_path": files.get("runtime_adapter_manifest_json_path", ""),
        "execution_artifact_index_md_path": files.get("execution_artifact_index_md_path", ""),
        "next_message": next_state.get("message", ""),
        "next_commands": next_state.get("commands") if isinstance(next_state.get("commands"), list) else [],
        "missing_required": missing_required,
        "checks": checks,
    }


def latest_v1_followup_check(out_root: Path) -> dict[str, Any]:
    latest_json_path = out_root.expanduser().resolve() / "reports" / "v1-followup-check-latest.json"
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "status": "missing",
            "action": "",
            "wait_reason": "",
            "next_gate_at": "",
            "seconds_until_next_gate": 0,
            "next_evidence_gate_reason": "",
            "next_evidence_gate_at": "",
            "seconds_until_next_evidence_gate": 0,
            "acceptance_wait_reason": "",
            "acceptance_gate_at": "",
            "seconds_until_acceptance_gate": 0,
            "latest_md_path": "",
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "status": "failed",
            "action": "",
            "wait_reason": "",
            "next_gate_at": "",
            "seconds_until_next_gate": 0,
            "next_evidence_gate_reason": "",
            "next_evidence_gate_at": "",
            "seconds_until_next_evidence_gate": 0,
            "acceptance_wait_reason": "",
            "acceptance_gate_at": "",
            "seconds_until_acceptance_gate": 0,
            "latest_md_path": "",
            "error": str(exc),
        }
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "status": data.get("status", ""),
        "action": data.get("action", ""),
        "wait_reason": data.get("wait_reason", ""),
        "next_gate_at": data.get("next_gate_at", ""),
        "seconds_until_next_gate": data.get("seconds_until_next_gate", 0),
        "next_evidence_gate_reason": data.get("next_evidence_gate_reason", data.get("wait_reason", "")),
        "next_evidence_gate_at": data.get("next_evidence_gate_at", data.get("next_gate_at", "")),
        "seconds_until_next_evidence_gate": data.get(
            "seconds_until_next_evidence_gate",
            data.get("seconds_until_next_gate", 0),
        ),
        "acceptance_wait_reason": data.get("acceptance_wait_reason", ""),
        "acceptance_gate_at": data.get("acceptance_gate_at", ""),
        "seconds_until_acceptance_gate": data.get("seconds_until_acceptance_gate", 0),
    }


def latest_v1_stage_status(out_root: Path) -> dict[str, Any]:
    latest_json_path = out_root.expanduser().resolve() / "reports" / "v1-stage-status-latest.json"
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "status": "missing",
            "ready": False,
            "latest_md_path": "",
            "summary": {},
            "next_gates": {},
            "stages": [],
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "status": "failed",
            "ready": False,
            "latest_md_path": "",
            "summary": {},
            "next_gates": {},
            "stages": [],
            "error": str(exc),
        }
    stages = data.get("stages") if isinstance(data.get("stages"), list) else []
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "status": data.get("status", ""),
        "ready": data.get("ready") is True,
        "decision": data.get("decision", ""),
        "created_at": data.get("created_at", ""),
        "summary": data.get("summary") if isinstance(data.get("summary"), dict) else {},
        "next_gates": data.get("next_gates") if isinstance(data.get("next_gates"), dict) else {},
        "stages": stages,
    }


def refresh_v1_stage_status_for_panel(out_root: Path) -> None:
    root = out_root.expanduser().resolve()
    reports = root / "reports"
    has_source_report = (reports / "runtime-health-latest.json").exists() or (
        reports / "v1-acceptance-latest.json"
    ).exists()
    if not has_source_report:
        return
    try:
        from .acceptance import run_v1_stage_status

        run_v1_stage_status(root)
    except Exception:
        return


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
