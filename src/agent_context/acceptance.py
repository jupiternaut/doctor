from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .codex_plus_smoke import run_codex_plus_smoke
from .io import ensure_dir, write_text
from .launchd import (
    run_semantic_launchd_audit,
    run_semantic_launchd_monitor,
    run_semantic_launchd_trend,
    wait_for_semantic_launchd_run,
)
from .mcp_live_smoke import run_mcp_live_smoke
from .reproducibility import latest_reproducibility_snapshot_status, run_reproducibility_snapshot
from .runtime_health import run_runtime_health, run_semantic_readiness


V1_ACCEPTANCE_VERSION = "0.1"
V1_FOLLOWUP_PLAN_VERSION = "0.1"
V1_FOLLOWUP_CHECK_VERSION = "0.1"
V1_STAGE_STATUS_VERSION = "0.1"
V1_REFRESH_VERSION = "0.1"


def run_v1_acceptance(
    out_root: Path,
    *,
    codex_plus_root: Path | None = None,
    refresh_health: bool = False,
    refresh_evidence: bool = False,
    min_documents: int = 1,
    min_projects: int = 1,
    min_sessions: int = 1,
    min_workflows: int = 1,
    min_semantic_chunks: int = 16,
    required_trend_days: int = 2,
    mcp_timeout_seconds: int = 60,
    codex_plus_timeout_seconds: int = 120,
    with_manager_feedback_smoke: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    codex_plus = codex_plus_root.expanduser().resolve() if codex_plus_root else None
    snapshot_roots = [root for root in [out_root, codex_plus] if root is not None]
    created_at = (now or datetime.now()).astimezone()
    refreshed_reports: dict[str, Any] = {}
    if refresh_evidence:
        refreshed_reports["semantic_launchd_monitor"] = compact_refresh_result(
            run_semantic_launchd_monitor(out_root, with_launchctl=True)
        )
        refreshed_reports["semantic_launchd_audit"] = compact_refresh_result(run_semantic_launchd_audit(out_root))
        refreshed_reports["semantic_launchd_trend"] = compact_refresh_result(
            run_semantic_launchd_trend(
                out_root,
                min_days=max(1, required_trend_days),
            )
        )
        semantic = run_semantic_readiness(
            out_root,
            min_semantic_chunks=max(0, min_semantic_chunks),
            required_trend_days=max(1, required_trend_days),
        )
        refreshed_reports["semantic_readiness"] = compact_refresh_result(semantic)
        snapshot = run_reproducibility_snapshot(out_root, roots=snapshot_roots)
        refreshed_reports["reproducibility_snapshot"] = compact_refresh_result(snapshot)
        if codex_plus:
            codex_smoke = run_codex_plus_smoke(
                out_root,
                codex_plus_root=codex_plus,
                timeout_seconds=max(5, codex_plus_timeout_seconds),
                run_manager_feedback=with_manager_feedback_smoke,
            )
            refreshed_reports["codex_plus_smoke"] = compact_refresh_result(codex_smoke)
        smoke = run_mcp_live_smoke(
            out_root,
            codex_plus_root=codex_plus,
            timeout_seconds=max(5, mcp_timeout_seconds),
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        )
        refreshed_reports["mcp_live_smoke"] = compact_refresh_result(smoke)
        runtime = run_runtime_health(
            out_root,
            codex_plus_root=codex_plus,
            min_documents=max(0, min_documents),
            min_projects=max(0, min_projects),
            min_sessions=max(0, min_sessions),
            min_workflows=max(0, min_workflows),
            min_semantic_chunks=max(0, min_semantic_chunks),
        )
        refreshed_reports["runtime_health"] = compact_refresh_result(runtime)
    elif refresh_health:
        semantic = run_semantic_readiness(
            out_root,
            min_semantic_chunks=max(0, min_semantic_chunks),
            required_trend_days=max(1, required_trend_days),
        )
        refreshed_reports["semantic_readiness"] = compact_refresh_result(semantic)
        runtime = run_runtime_health(
            out_root,
            codex_plus_root=codex_plus,
            min_documents=max(0, min_documents),
            min_projects=max(0, min_projects),
            min_sessions=max(0, min_sessions),
            min_workflows=max(0, min_workflows),
            min_semantic_chunks=max(0, min_semantic_chunks),
        )
        refreshed_reports["runtime_health"] = compact_refresh_result(runtime)
    else:
        runtime = read_latest_json(out_root / "reports" / "runtime-health-latest.json")
        semantic = read_latest_json(out_root / "reports" / "semantic-readiness-latest.json")
    mcp_smoke = read_latest_json(out_root / "reports" / "mcp-live-smoke-latest.json")
    reproducibility = latest_reproducibility_snapshot_status(
        out_root,
        snapshot_roots,
    )
    evidence = build_evidence(runtime, semantic, mcp_smoke, reproducibility)
    status = acceptance_status(runtime, semantic, evidence)
    report_id = created_at.strftime("%Y%m%d%H%M%S%f")
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"v1-acceptance-{report_id}.json"
    md_path = reports_dir / f"v1-acceptance-{report_id}.md"
    latest_json_path = reports_dir / "v1-acceptance-latest.json"
    latest_md_path = reports_dir / "v1-acceptance-latest.md"
    report = {
        "v1_acceptance_version": V1_ACCEPTANCE_VERSION,
        "created_at": created_at.isoformat(),
        "out_root": str(out_root),
        "codex_plus_root": str(codex_plus or ""),
        "status": status,
        "ready": status == "ok",
        "refresh_health": refresh_health or refresh_evidence,
        "refresh_evidence": refresh_evidence,
        "with_manager_feedback_smoke": with_manager_feedback_smoke,
        "refreshed_reports": refreshed_reports,
        "decision": acceptance_decision(status, semantic),
        "evidence": evidence,
        "acceptance_matrix": runtime.get("acceptance_matrix") or [],
        "next_commands": next_commands(out_root, codex_plus, with_manager_feedback_smoke=with_manager_feedback_smoke),
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    followup_plan = write_v1_followup_plan(
        report,
        semantic,
        out_root=out_root,
        codex_plus_root=codex_plus,
        report_id=report_id,
        created_at=created_at,
    )
    report["followup_plan"] = compact_followup_plan(followup_plan)
    report["followup_json_path"] = followup_plan["json_path"]
    report["followup_md_path"] = followup_plan["md_path"]
    report["latest_followup_json_path"] = followup_plan["latest_json_path"]
    report["latest_followup_md_path"] = followup_plan["latest_md_path"]
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_v1_acceptance_markdown(report)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return report


def write_v1_followup_plan(
    acceptance_report: dict[str, Any],
    semantic: dict[str, Any],
    *,
    out_root: Path,
    codex_plus_root: Path | None,
    report_id: str,
    created_at: datetime,
) -> dict[str, Any]:
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"v1-followup-{report_id}.json"
    md_path = reports_dir / f"v1-followup-{report_id}.md"
    latest_json_path = reports_dir / "v1-followup-latest.json"
    latest_md_path = reports_dir / "v1-followup-latest.md"
    readiness = semantic.get("readiness") if isinstance(semantic.get("readiness"), dict) else {}
    earliest = str(readiness.get("earliest_multi_day_check_after") or "")
    next_monitor_due = str(readiness.get("next_monitor_due_at") or "")
    status = str(acceptance_report.get("status") or "")
    ready = acceptance_report.get("ready") is True
    can_recheck = can_recheck_now(status, earliest, created_at)
    plan = {
        "v1_followup_plan_version": V1_FOLLOWUP_PLAN_VERSION,
        "created_at": created_at.isoformat(),
        "out_root": str(out_root),
        "codex_plus_root": str(codex_plus_root or ""),
        "status": status,
        "ready": ready,
        "reason": acceptance_report.get("decision") or "",
        "can_recheck_now": can_recheck,
        "earliest_recheck_after": earliest,
        "next_monitor_due_at": next_monitor_due,
        "trend_days_observed": readiness.get("trend_days_observed", 0),
        "trend_days_remaining": readiness.get("trend_days_remaining", 0),
        "semantic_readiness_status": semantic.get("status") or "",
        "semantic_readiness_latest_md_path": semantic.get("latest_md_path") or semantic.get("md_path") or "",
        "runtime_health_latest_md_path": next(
            (
                str(item.get("path") or "")
                for item in acceptance_report.get("evidence") or []
                if item.get("id") == "runtime_health"
            ),
            "",
        ),
        "acceptance_latest_md_path": acceptance_report.get("latest_md_path") or "",
        "commands": acceptance_report.get("next_commands") if isinstance(acceptance_report.get("next_commands"), list) else [],
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    action = "complete" if ready or status == "ok" else "wait"
    evidence_gate = followup_wait_state(plan, created_at, can_recheck, action=action)
    acceptance_gate = followup_acceptance_wait_state(plan, created_at, can_recheck, action=action)
    plan.update(
        {
            "next_evidence_gate_reason": evidence_gate["wait_reason"],
            "next_evidence_gate_at": evidence_gate["next_gate_at"],
            "seconds_until_next_evidence_gate": evidence_gate["seconds_until_next_gate"],
            "acceptance_wait_reason": acceptance_gate["acceptance_wait_reason"],
            "acceptance_gate_at": acceptance_gate["acceptance_gate_at"],
            "seconds_until_acceptance_gate": acceptance_gate["seconds_until_acceptance_gate"],
        }
    )
    payload = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_v1_followup_markdown(plan)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return plan


def compact_followup_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": plan.get("status") or "",
        "ready": plan.get("ready") is True,
        "can_recheck_now": plan.get("can_recheck_now") is True,
        "earliest_recheck_after": plan.get("earliest_recheck_after") or "",
        "next_monitor_due_at": plan.get("next_monitor_due_at") or "",
        "next_evidence_gate_reason": plan.get("next_evidence_gate_reason") or "",
        "next_evidence_gate_at": plan.get("next_evidence_gate_at") or "",
        "seconds_until_next_evidence_gate": plan.get("seconds_until_next_evidence_gate", 0),
        "acceptance_wait_reason": plan.get("acceptance_wait_reason") or "",
        "acceptance_gate_at": plan.get("acceptance_gate_at") or "",
        "seconds_until_acceptance_gate": plan.get("seconds_until_acceptance_gate", 0),
        "trend_days_remaining": plan.get("trend_days_remaining", 0),
        "latest_md_path": plan.get("latest_md_path") or "",
        "latest_json_path": plan.get("latest_json_path") or "",
    }


def can_recheck_now(status: str, earliest_recheck_after: str, created_at: datetime) -> bool:
    if status != "waiting_for_time":
        return True
    if not earliest_recheck_after:
        return False
    try:
        earliest = datetime.fromisoformat(earliest_recheck_after)
    except ValueError:
        return False
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=created_at.tzinfo)
    current = created_at
    if current.tzinfo is None:
        current = current.replace(tzinfo=earliest.tzinfo)
    return current >= earliest


def run_v1_followup(
    out_root: Path,
    *,
    codex_plus_root: Path | None = None,
    run_when_ready: bool = False,
    force: bool = False,
    min_documents: int = 1,
    min_projects: int = 1,
    min_sessions: int = 1,
    min_workflows: int = 1,
    min_semantic_chunks: int = 16,
    required_trend_days: int = 2,
    mcp_timeout_seconds: int = 60,
    codex_plus_timeout_seconds: int = 120,
    with_manager_feedback_smoke: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    codex_plus = codex_plus_root.expanduser().resolve() if codex_plus_root else None
    created_at = (now or datetime.now()).astimezone()
    plan = read_latest_json(out_root / "reports" / "v1-followup-latest.json")
    generated_plan = False
    acceptance_result: dict[str, Any] | None = None
    if plan.get("status") == "missing":
        acceptance_result = run_v1_acceptance(
            out_root,
            codex_plus_root=codex_plus,
            min_documents=min_documents,
            min_projects=min_projects,
            min_sessions=min_sessions,
            min_workflows=min_workflows,
            min_semantic_chunks=min_semantic_chunks,
            required_trend_days=required_trend_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            codex_plus_timeout_seconds=codex_plus_timeout_seconds,
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        )
        plan = read_latest_json(out_root / "reports" / "v1-followup-latest.json")
        generated_plan = True
    current_can_recheck = can_recheck_now(
        str(plan.get("status") or ""),
        str(plan.get("earliest_recheck_after") or ""),
        created_at,
    )
    action = "wait"
    if plan.get("ready") is True or plan.get("status") == "ok":
        action = "complete"
    elif force or (run_when_ready and current_can_recheck):
        action = "rechecked"
        acceptance_result = run_v1_acceptance(
            out_root,
            codex_plus_root=codex_plus,
            refresh_evidence=True,
            min_documents=min_documents,
            min_projects=min_projects,
            min_sessions=min_sessions,
            min_workflows=min_workflows,
            min_semantic_chunks=min_semantic_chunks,
            required_trend_days=required_trend_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            codex_plus_timeout_seconds=codex_plus_timeout_seconds,
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        )
        plan = read_latest_json(out_root / "reports" / "v1-followup-latest.json")
        current_can_recheck = can_recheck_now(
            str(plan.get("status") or ""),
            str(plan.get("earliest_recheck_after") or ""),
            created_at,
        )
        if acceptance_result.get("ready") is True:
            action = "complete"
    status = followup_check_status(action, plan)
    wait_state = followup_wait_state(plan, created_at, current_can_recheck, action=action)
    acceptance_wait_state = followup_acceptance_wait_state(
        plan,
        created_at,
        current_can_recheck,
        action=action,
    )
    report_id = created_at.strftime("%Y%m%d%H%M%S%f")
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"v1-followup-check-{report_id}.json"
    md_path = reports_dir / f"v1-followup-check-{report_id}.md"
    latest_json_path = reports_dir / "v1-followup-check-latest.json"
    latest_md_path = reports_dir / "v1-followup-check-latest.md"
    report = {
        "v1_followup_check_version": V1_FOLLOWUP_CHECK_VERSION,
        "created_at": created_at.isoformat(),
        "out_root": str(out_root),
        "codex_plus_root": str(codex_plus or ""),
        "status": status,
        "action": action,
        "ready": plan.get("ready") is True or (acceptance_result or {}).get("ready") is True,
        "generated_plan": generated_plan,
        "run_when_ready": run_when_ready,
        "force": force,
        "with_manager_feedback_smoke": with_manager_feedback_smoke,
        "can_recheck_now": current_can_recheck,
        "wait_reason": wait_state["wait_reason"],
        "next_gate_at": wait_state["next_gate_at"],
        "seconds_until_next_gate": wait_state["seconds_until_next_gate"],
        "next_evidence_gate_reason": wait_state["wait_reason"],
        "next_evidence_gate_at": wait_state["next_gate_at"],
        "seconds_until_next_evidence_gate": wait_state["seconds_until_next_gate"],
        "acceptance_wait_reason": acceptance_wait_state["acceptance_wait_reason"],
        "acceptance_gate_at": acceptance_wait_state["acceptance_gate_at"],
        "seconds_until_acceptance_gate": acceptance_wait_state["seconds_until_acceptance_gate"],
        "earliest_recheck_after": plan.get("earliest_recheck_after") or "",
        "next_monitor_due_at": plan.get("next_monitor_due_at") or "",
        "trend_days_remaining": plan.get("trend_days_remaining", 0),
        "followup_plan_latest_md_path": plan.get("latest_md_path") or "",
        "acceptance_latest_md_path": (acceptance_result or {}).get("latest_md_path")
        or plan.get("acceptance_latest_md_path")
        or "",
        "acceptance_status": (acceptance_result or {}).get("status") or plan.get("status") or "",
        "next_command": first_followup_command(plan),
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_v1_followup_check_markdown(report)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return report


def run_v1_refresh(
    out_root: Path,
    *,
    codex_plus_root: Path | None = None,
    force: bool = False,
    refresh_semantic_evidence: bool = True,
    refresh_mcp_smoke: bool = True,
    refresh_runtime_health: bool = True,
    min_documents: int = 1,
    min_projects: int = 1,
    min_sessions: int = 1,
    min_workflows: int = 1,
    min_semantic_chunks: int = 16,
    required_trend_days: int = 2,
    mcp_timeout_seconds: int = 60,
    codex_plus_timeout_seconds: int = 120,
    with_manager_feedback_smoke: bool = False,
    wait_for_semantic_evidence: bool = False,
    semantic_wait_timeout_seconds: int = 7200,
    semantic_wait_poll_seconds: int = 60,
    now: datetime | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    codex_plus = codex_plus_root.expanduser().resolve() if codex_plus_root else None
    created_at = (now or datetime.now()).astimezone()
    semantic_evidence = run_v1_semantic_evidence_refresh_if_due(
        out_root,
        now=created_at,
        enabled=refresh_semantic_evidence,
        wait_for_semantic_evidence=wait_for_semantic_evidence,
        wait_timeout_seconds=max(0, semantic_wait_timeout_seconds),
        wait_poll_seconds=max(1, semantic_wait_poll_seconds),
        min_semantic_chunks=max(0, min_semantic_chunks),
        required_trend_days=max(1, required_trend_days),
    )
    followup_now = datetime.now().astimezone() if semantic_evidence.get("wait") else created_at
    mcp_smoke: dict[str, Any] | None = None
    if refresh_mcp_smoke:
        mcp_smoke = run_mcp_live_smoke(
            out_root,
            codex_plus_root=codex_plus,
            timeout_seconds=max(5, mcp_timeout_seconds),
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        )
    runtime: dict[str, Any] | None = None
    if refresh_runtime_health:
        runtime = run_runtime_health(
            out_root,
            codex_plus_root=codex_plus,
            min_documents=max(0, min_documents),
            min_projects=max(0, min_projects),
            min_sessions=max(0, min_sessions),
            min_workflows=max(0, min_workflows),
            min_semantic_chunks=max(0, min_semantic_chunks),
        )
    followup = run_v1_followup(
        out_root,
        codex_plus_root=codex_plus,
        run_when_ready=True,
        force=force,
        min_documents=min_documents,
        min_projects=min_projects,
        min_sessions=min_sessions,
        min_workflows=min_workflows,
        min_semantic_chunks=min_semantic_chunks,
        required_trend_days=required_trend_days,
        mcp_timeout_seconds=mcp_timeout_seconds,
        codex_plus_timeout_seconds=codex_plus_timeout_seconds,
        with_manager_feedback_smoke=with_manager_feedback_smoke,
        now=followup_now,
    )
    # Keep the local HTML/status panel synchronized with the follow-up result.
    from .panel import build_context_panel

    panel = build_context_panel(out_root, auto_context=False)
    stage = run_v1_stage_status(out_root, codex_plus_root=codex_plus)
    status = str(stage.get("status") or followup.get("status") or "missing")
    ready = stage.get("ready") is True or followup.get("ready") is True
    report_id = created_at.strftime("%Y%m%d%H%M%S%f")
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"v1-refresh-{report_id}.json"
    md_path = reports_dir / f"v1-refresh-{report_id}.md"
    latest_json_path = reports_dir / "v1-refresh-latest.json"
    latest_md_path = reports_dir / "v1-refresh-latest.md"
    report = {
        "v1_refresh_version": V1_REFRESH_VERSION,
        "created_at": created_at.isoformat(),
        "out_root": str(out_root),
        "codex_plus_root": str(codex_plus or ""),
        "status": status,
        "ready": ready,
        "force": force,
        "refresh_semantic_evidence": refresh_semantic_evidence,
        "refresh_mcp_smoke": refresh_mcp_smoke,
        "refresh_runtime_health": refresh_runtime_health,
        "wait_for_semantic_evidence": wait_for_semantic_evidence,
        "with_manager_feedback_smoke": with_manager_feedback_smoke,
        "semantic_evidence": semantic_evidence,
        "mcp_live_smoke": compact_v1_refresh_mcp_smoke(mcp_smoke),
        "runtime_health": compact_v1_refresh_runtime(runtime),
        "followup_check": compact_v1_refresh_followup(followup),
        "stage_status": compact_v1_refresh_stage(stage),
        "panel": compact_v1_refresh_panel(panel),
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_v1_refresh_markdown(report)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return report


def run_v1_semantic_evidence_refresh_if_due(
    out_root: Path,
    *,
    now: datetime,
    enabled: bool,
    wait_for_semantic_evidence: bool = False,
    wait_timeout_seconds: int = 7200,
    wait_poll_seconds: int = 60,
    min_semantic_chunks: int,
    required_trend_days: int,
) -> dict[str, Any]:
    if not enabled:
        return {"refreshed": False, "reason": "disabled"}
    gate = latest_semantic_evidence_gate(out_root)
    if wait_for_semantic_evidence:
        wait_result = wait_for_semantic_launchd_run(
            out_root,
            timeout_seconds=max(0, wait_timeout_seconds),
            poll_seconds=max(1, wait_poll_seconds),
        )
        if wait_result.get("status") == "ok":
            refreshed = run_v1_semantic_evidence_refresh(
                out_root,
                gate=gate,
                min_semantic_chunks=min_semantic_chunks,
                required_trend_days=required_trend_days,
            )
            refreshed["wait"] = compact_refresh_result(wait_result)
            return refreshed
        due_at = parse_iso_datetime(gate, now) if gate else None
        wait = wait_until("monitor_not_due", due_at, now) if due_at else {}
        return {
            "refreshed": False,
            "reason": "semantic_wait_timeout",
            "next_gate_at": str(wait.get("next_gate_at") or gate),
            "seconds_until_next_gate": int(wait.get("seconds_until_next_gate") or 0),
            "wait": compact_refresh_result(wait_result),
        }
    if not gate:
        return {"refreshed": False, "reason": "missing_next_monitor_due_at"}
    due_at = parse_iso_datetime(gate, now)
    if due_at and now < due_at:
        wait = wait_until("monitor_not_due", due_at, now)
        return {
            "refreshed": False,
            "reason": wait["wait_reason"],
            "next_gate_at": wait["next_gate_at"],
            "seconds_until_next_gate": wait["seconds_until_next_gate"],
        }
    return run_v1_semantic_evidence_refresh(
        out_root,
        gate=gate,
        min_semantic_chunks=min_semantic_chunks,
        required_trend_days=required_trend_days,
    )


def run_v1_semantic_evidence_refresh(
    out_root: Path,
    *,
    gate: str,
    min_semantic_chunks: int,
    required_trend_days: int,
) -> dict[str, Any]:
    monitor = run_semantic_launchd_monitor(out_root, with_launchctl=True)
    audit = run_semantic_launchd_audit(out_root)
    trend = run_semantic_launchd_trend(out_root, min_days=max(1, required_trend_days))
    readiness = run_semantic_readiness(
        out_root,
        min_semantic_chunks=max(0, min_semantic_chunks),
        required_trend_days=max(1, required_trend_days),
        include_launchctl=True,
    )
    readiness_payload = readiness.get("readiness") if isinstance(readiness.get("readiness"), dict) else {}
    next_gate_at = str(readiness_payload.get("next_monitor_due_at") or gate)
    return {
        "refreshed": True,
        "reason": "monitor_due",
        "consumed_gate_at": gate,
        "next_gate_at": next_gate_at,
        "monitor": compact_refresh_result(monitor),
        "audit": compact_refresh_result(audit),
        "trend": compact_refresh_result(trend),
        "readiness": compact_refresh_result(readiness),
    }


def latest_semantic_evidence_gate(out_root: Path) -> str:
    reports_dir = out_root.expanduser().resolve() / "reports"
    followup = read_latest_json(reports_dir / "v1-followup-latest.json")
    gate = str(followup.get("next_monitor_due_at") or "")
    if gate:
        return gate
    followup_plan = followup.get("followup_plan") if isinstance(followup.get("followup_plan"), dict) else {}
    gate = str(followup_plan.get("next_monitor_due_at") or "")
    if gate:
        return gate
    semantic = read_latest_json(reports_dir / "semantic-readiness-latest.json")
    readiness = semantic.get("readiness") if isinstance(semantic.get("readiness"), dict) else {}
    return str(readiness.get("next_monitor_due_at") or "")


def run_v1_stage_status(
    out_root: Path,
    *,
    codex_plus_root: Path | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    codex_plus = codex_plus_root.expanduser().resolve() if codex_plus_root else None
    created_at = datetime.now().astimezone()
    reports_dir = ensure_dir(out_root / "reports")
    runtime = read_latest_json(reports_dir / "runtime-health-latest.json")
    acceptance = read_latest_json(reports_dir / "v1-acceptance-latest.json")
    followup_check = read_latest_json(reports_dir / "v1-followup-check-latest.json")
    semantic = read_latest_json(reports_dir / "semantic-readiness-latest.json")
    launchd_monitor = read_latest_json(reports_dir / "semantic-launchd-monitor-latest.json")
    mcp_smoke = read_latest_json(reports_dir / "mcp-live-smoke-latest.json")
    codex_plus_smoke = read_latest_json(reports_dir / "codex-plus-smoke-latest.json")
    feedback_trend = read_latest_matching_json(reports_dir, "feedback_replay_trend_*.json")
    stages = build_v1_stage_rows(
        runtime=runtime,
        acceptance=acceptance,
        followup_check=followup_check,
        semantic=semantic,
        launchd_monitor=launchd_monitor,
        mcp_smoke=mcp_smoke,
        codex_plus_smoke=codex_plus_smoke,
        feedback_trend=feedback_trend,
    )
    report_id = created_at.strftime("%Y%m%d%H%M%S%f")
    json_path = reports_dir / f"v1-stage-status-{report_id}.json"
    md_path = reports_dir / f"v1-stage-status-{report_id}.md"
    latest_json_path = reports_dir / "v1-stage-status-latest.json"
    latest_md_path = reports_dir / "v1-stage-status-latest.md"
    report = {
        "v1_stage_status_version": V1_STAGE_STATUS_VERSION,
        "created_at": created_at.isoformat(),
        "out_root": str(out_root),
        "codex_plus_root": str(codex_plus or ""),
        "status": v1_stage_overall_status(acceptance, runtime, stages),
        "ready": acceptance.get("ready") is True,
        "decision": acceptance.get("decision") or "",
        "summary": summarize_stages(stages),
        "next_gates": v1_stage_next_gates(acceptance, followup_check),
        "stages": stages,
        "source_reports": {
            "runtime_health": report_ref(runtime, reports_dir / "runtime-health-latest.json"),
            "v1_acceptance": report_ref(acceptance, reports_dir / "v1-acceptance-latest.json"),
            "v1_followup_check": report_ref(followup_check, reports_dir / "v1-followup-check-latest.json"),
            "semantic_readiness": report_ref(semantic, reports_dir / "semantic-readiness-latest.json"),
            "semantic_launchd_monitor": report_ref(
                launchd_monitor,
                reports_dir / "semantic-launchd-monitor-latest.json",
            ),
            "mcp_live_smoke": report_ref(mcp_smoke, reports_dir / "mcp-live-smoke-latest.json"),
            "codex_plus_smoke": report_ref(codex_plus_smoke, reports_dir / "codex-plus-smoke-latest.json"),
            "feedback_replay_trend": report_ref(feedback_trend, Path(feedback_trend.get("path") or "")),
        },
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_v1_stage_status_markdown(report)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return report


def compact_v1_refresh_followup(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status") or "",
        "action": report.get("action") or "",
        "ready": report.get("ready") is True,
        "can_recheck_now": report.get("can_recheck_now") is True,
        "wait_reason": report.get("wait_reason") or "",
        "next_gate_at": report.get("next_gate_at") or "",
        "seconds_until_next_gate": report.get("seconds_until_next_gate", 0),
        "acceptance_wait_reason": report.get("acceptance_wait_reason") or "",
        "acceptance_gate_at": report.get("acceptance_gate_at") or "",
        "seconds_until_acceptance_gate": report.get("seconds_until_acceptance_gate", 0),
        "latest_md_path": report.get("latest_md_path") or "",
        "latest_json_path": report.get("latest_json_path") or "",
    }


def compact_v1_refresh_runtime(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"refreshed": False}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "refreshed": True,
        "status": report.get("status") or "",
        "summary": summary,
        "latest_md_path": report.get("latest_md_path") or "",
        "latest_json_path": report.get("latest_json_path") or "",
    }


def compact_v1_refresh_mcp_smoke(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"refreshed": False}
    return {
        "refreshed": True,
        "status": report.get("status") or "",
        "tools_total": report.get("tools_total") or 0,
        "required_tools_missing": report.get("required_tools_missing") or [],
        "latest_md_path": report.get("latest_md_path") or "",
        "latest_json_path": report.get("latest_json_path") or "",
    }


def compact_v1_refresh_stage(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    gates = report.get("next_gates") if isinstance(report.get("next_gates"), dict) else {}
    return {
        "status": report.get("status") or "",
        "ready": report.get("ready") is True,
        "summary": summary,
        "next_gates": gates,
        "latest_md_path": report.get("latest_md_path") or "",
        "latest_json_path": report.get("latest_json_path") or "",
    }


def compact_v1_refresh_panel(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "panel_version": report.get("panel_version") or "",
        "status_json_path": report.get("status_json_path") or "",
        "html_path": report.get("html_path") or "",
        "auto_context": report.get("auto_context") is True,
        "last_generated_pack": report.get("last_generated_pack") or "",
        "last_sources_jsonl": report.get("last_sources_jsonl") or "",
    }


def read_latest_matching_json(directory: Path, pattern: str) -> dict[str, Any]:
    paths = sorted(path for path in directory.glob(pattern) if path.is_file())
    if not paths:
        return {"exists": False, "path": str(directory / pattern), "status": "missing"}
    path = paths[-1]
    payload = read_latest_json(path)
    payload.setdefault("path", str(path))
    payload.setdefault("latest_json_path", str(path))
    md_path = path.with_suffix(".md")
    if md_path.exists():
        payload.setdefault("latest_md_path", str(md_path))
    return payload


def build_v1_stage_rows(
    *,
    runtime: dict[str, Any],
    acceptance: dict[str, Any],
    followup_check: dict[str, Any],
    semantic: dict[str, Any],
    launchd_monitor: dict[str, Any],
    mcp_smoke: dict[str, Any],
    codex_plus_smoke: dict[str, Any],
    feedback_trend: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = {
        str(item.get("id") or ""): item
        for item in runtime.get("checks", [])
        if isinstance(item, dict)
    }
    stages = [
        stage_from_runtime_check("downloads_ingestion", "Downloads ingestion", checks.get("downloads_ingestion")),
        stage_from_runtime_check("provider_layer", "Provider layer", checks.get("provider_layer")),
        stage_from_runtime_check("cold_indexes", "Cold indexes / RAG", checks.get("cold_indexes")),
        semantic_stage(checks.get("semantic_background"), semantic, launchd_monitor),
        stage_from_runtime_check("hot_context_pack", "Hot context pack / resolver", checks.get("hot_context_pack")),
        stage_from_runtime_check("mcp_surface", "MCP surface", checks.get("mcp_surface"), extra=mcp_smoke),
        feedback_stage(checks.get("feedback_loop"), feedback_trend),
        stage_from_runtime_check(
            "codex_plus_integration",
            "Codex++ integration",
            checks.get("codex_plus_integration"),
            extra=codex_plus_smoke,
        ),
        stage_from_runtime_check("safety_permissions", "Safety / permissions", checks.get("safety_permissions")),
        acceptance_gate_stage(acceptance, followup_check),
    ]
    return stages


def stage_from_runtime_check(
    stage_id: str,
    title: str,
    check: dict[str, Any] | None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not check:
        return {
            "id": stage_id,
            "title": title,
            "status": "missing",
            "progress": 0,
            "summary": "runtime-health check is missing",
            "evidence_paths": [],
            "next_action": "Run agent-context runtime-health.",
        }
    evidence_paths = collect_path_values(check.get("evidence"))[:12]
    if extra:
        evidence_paths.extend(collect_path_values(extra)[:4])
    evidence_paths = unique_strings(evidence_paths)
    status = str(check.get("status") or "missing")
    return {
        "id": stage_id,
        "title": title,
        "status": status,
        "progress": progress_for_status(status),
        "summary": str(check.get("summary") or ""),
        "evidence_paths": evidence_paths,
        "next_action": str(check.get("next_action") or ""),
    }


def semantic_stage(
    check: dict[str, Any] | None,
    semantic: dict[str, Any],
    launchd_monitor: dict[str, Any],
) -> dict[str, Any]:
    readiness = semantic.get("readiness") if isinstance(semantic.get("readiness"), dict) else {}
    status = str(semantic.get("status") or (check or {}).get("status") or "missing")
    evidence_paths = unique_strings(
        [
            *collect_path_values((check or {}).get("evidence")),
            str(semantic.get("latest_md_path") or semantic.get("md_path") or ""),
            str(launchd_monitor.get("latest_md_path") or launchd_monitor.get("md_path") or ""),
        ]
    )
    return {
        "id": "semantic_background",
        "title": "Background semantic index",
        "status": status,
        "progress": progress_for_status(status),
        "summary": semantic_summary(readiness),
        "evidence_paths": [path for path in evidence_paths if path],
        "next_action": str(semantic.get("next_action") or (check or {}).get("next_action") or ""),
    }


def feedback_stage(check: dict[str, Any] | None, trend: dict[str, Any]) -> dict[str, Any]:
    stage = stage_from_runtime_check("feedback_loop", "Feedback loop", check, extra=trend)
    trend_summary = trend.get("summary") if isinstance(trend.get("summary"), dict) else {}
    if trend_summary:
        stage["summary"] = (
            f"{stage['summary']}; replay_health={trend_summary.get('health', '')}, "
            f"latest_expected_top1_rate={trend_summary.get('latest_expected_top1_rate', '')}"
        ).strip("; ")
    return stage


def acceptance_gate_stage(acceptance: dict[str, Any], followup_check: dict[str, Any]) -> dict[str, Any]:
    status = str(acceptance.get("status") or "missing")
    gates = v1_stage_next_gates(acceptance, followup_check)
    next_action = str((followup_check.get("next_command") if isinstance(followup_check, dict) else "") or "")
    if not next_action and status == "missing":
        next_action = "Run agent-context v1-acceptance."
    return {
        "id": "v1_acceptance_gate",
        "title": "V1 acceptance gate",
        "status": status,
        "progress": progress_for_status(status),
        "summary": str(acceptance.get("decision") or "latest v1 acceptance report is missing"),
        "evidence_paths": unique_strings(
            [
                str(acceptance.get("latest_md_path") or acceptance.get("md_path") or ""),
                str(followup_check.get("latest_md_path") or followup_check.get("md_path") or ""),
            ]
        ),
        "next_action": next_action,
        "next_gates": gates,
    }


def progress_for_status(status: str) -> int:
    return {
        "ok": 100,
        "complete": 100,
        "waiting_for_time": 90,
        "rechecked": 85,
        "warning": 70,
        "short_window": 70,
        "missing": 0,
        "failed": 0,
        "alert": 0,
        "attention_required": 0,
    }.get(status, 50)


def collect_path_values(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str) and path_like_key(key) and child:
                paths.append(child)
            else:
                paths.extend(collect_path_values(child))
    elif isinstance(value, list):
        for child in value:
            paths.extend(collect_path_values(child))
    elif isinstance(value, str) and looks_like_path(value):
        paths.append(value)
    return paths


def path_like_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ["path", "jsonl", "sqlite", "report", "dir"])


def looks_like_path(value: str) -> bool:
    return value.startswith("/") or any(value.endswith(suffix) for suffix in [".json", ".jsonl", ".md", ".sqlite"])


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def summarize_stages(stages: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for stage in stages:
        status = str(stage.get("status") or "missing")
        counts[status] = counts.get(status, 0) + 1
    return {
        "stages_total": len(stages),
        "ok": counts.get("ok", 0),
        "waiting_for_time": counts.get("waiting_for_time", 0),
        "warning": counts.get("warning", 0),
        "missing": counts.get("missing", 0),
        "failed": counts.get("failed", 0),
        "status_counts": counts,
    }


def v1_stage_overall_status(
    acceptance: dict[str, Any],
    runtime: dict[str, Any],
    stages: list[dict[str, Any]],
) -> str:
    if acceptance.get("status") and acceptance.get("status") != "missing":
        return str(acceptance.get("status"))
    if runtime.get("status") and runtime.get("status") != "missing":
        return str(runtime.get("status"))
    if any(stage.get("status") in {"failed", "alert", "attention_required"} for stage in stages):
        return "failed"
    if any(stage.get("status") == "missing" for stage in stages):
        return "missing"
    if any(stage.get("status") == "warning" for stage in stages):
        return "warning"
    return "ok"


def v1_stage_next_gates(acceptance: dict[str, Any], followup_check: dict[str, Any]) -> dict[str, Any]:
    followup_plan = acceptance.get("followup_plan") if isinstance(acceptance.get("followup_plan"), dict) else {}
    evidence_reason = followup_check.get("wait_reason") or followup_plan.get("next_evidence_gate_reason") or ""
    evidence_at = followup_check.get("next_gate_at") or followup_plan.get("next_evidence_gate_at") or ""
    seconds_until_evidence = followup_check.get("seconds_until_next_gate")
    if seconds_until_evidence is None:
        seconds_until_evidence = followup_plan.get("seconds_until_next_evidence_gate", 0)
    acceptance_reason = (
        followup_check.get("acceptance_wait_reason") or followup_plan.get("acceptance_wait_reason") or ""
    )
    acceptance_at = followup_check.get("acceptance_gate_at") or followup_plan.get("acceptance_gate_at") or ""
    seconds_until_acceptance = followup_check.get("seconds_until_acceptance_gate")
    if seconds_until_acceptance is None:
        seconds_until_acceptance = followup_plan.get("seconds_until_acceptance_gate", 0)
    return {
        # Compatibility fields used by existing Codex++ consumers. They refer to the
        # next background evidence gate, not the final v1 acceptance gate.
        "wait_reason": evidence_reason,
        "next_gate_at": evidence_at,
        "seconds_until_next_gate": seconds_until_evidence,
        "next_evidence_gate_reason": evidence_reason,
        "next_evidence_gate_at": evidence_at,
        "seconds_until_next_evidence_gate": seconds_until_evidence,
        "acceptance_wait_reason": acceptance_reason,
        "acceptance_gate_at": acceptance_at,
        "seconds_until_acceptance_gate": seconds_until_acceptance,
        "trend_days_remaining": followup_check.get("trend_days_remaining")
        if "trend_days_remaining" in followup_check
        else followup_plan.get("trend_days_remaining", 0),
    }


def report_ref(report: dict[str, Any], fallback_path: Path) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "exists": report.get("exists", fallback_path.exists()),
        "status": report.get("status") or summary.get("health") or "",
        "created_at": report.get("created_at") or "",
        "json_path": str(report.get("latest_json_path") or report.get("json_path") or report.get("path") or fallback_path),
        "md_path": str(report.get("latest_md_path") or report.get("md_path") or ""),
    }


def followup_check_status(action: str, plan: dict[str, Any]) -> str:
    if action == "complete":
        return "ok"
    if action == "rechecked":
        return "rechecked"
    if plan.get("status") == "failed":
        return "failed"
    if plan.get("status") == "missing":
        return "missing"
    return "waiting_for_time"


def first_followup_command(plan: dict[str, Any]) -> str:
    commands = plan.get("commands") if isinstance(plan.get("commands"), list) else []
    return str(commands[0]) if commands else ""


def followup_wait_state(
    plan: dict[str, Any],
    now: datetime,
    can_recheck: bool,
    *,
    action: str,
) -> dict[str, Any]:
    if action == "complete" or plan.get("ready") is True:
        return {"wait_reason": "complete", "next_gate_at": "", "seconds_until_next_gate": 0}
    if action == "rechecked":
        return {"wait_reason": "rechecked", "next_gate_at": "", "seconds_until_next_gate": 0}
    if can_recheck:
        return {"wait_reason": "ready_to_recheck", "next_gate_at": "", "seconds_until_next_gate": 0}
    monitor_due = parse_iso_datetime(str(plan.get("next_monitor_due_at") or ""), now)
    earliest = parse_iso_datetime(str(plan.get("earliest_recheck_after") or ""), now)
    if monitor_due and now < monitor_due:
        return wait_until("monitor_not_due", monitor_due, now)
    if earliest and now < earliest:
        return wait_until("multi_day_not_due", earliest, now)
    return {"wait_reason": "waiting_for_evidence", "next_gate_at": "", "seconds_until_next_gate": 0}


def followup_acceptance_wait_state(
    plan: dict[str, Any],
    now: datetime,
    can_recheck: bool,
    *,
    action: str,
) -> dict[str, Any]:
    if action == "complete" or plan.get("ready") is True:
        return {
            "acceptance_wait_reason": "complete",
            "acceptance_gate_at": "",
            "seconds_until_acceptance_gate": 0,
        }
    if action == "rechecked":
        return {
            "acceptance_wait_reason": "rechecked",
            "acceptance_gate_at": "",
            "seconds_until_acceptance_gate": 0,
        }
    if can_recheck:
        return {
            "acceptance_wait_reason": "ready_to_recheck",
            "acceptance_gate_at": "",
            "seconds_until_acceptance_gate": 0,
        }
    earliest = parse_iso_datetime(str(plan.get("earliest_recheck_after") or ""), now)
    if earliest and now < earliest:
        return acceptance_wait_until("multi_day_not_due", earliest, now)
    return {
        "acceptance_wait_reason": "waiting_for_evidence",
        "acceptance_gate_at": "",
        "seconds_until_acceptance_gate": 0,
    }


def wait_until(reason: str, target: datetime, now: datetime) -> dict[str, Any]:
    return {
        "wait_reason": reason,
        "next_gate_at": target.isoformat(),
        "seconds_until_next_gate": max(0, int((target - now).total_seconds())),
    }


def acceptance_wait_until(reason: str, target: datetime, now: datetime) -> dict[str, Any]:
    return {
        "acceptance_wait_reason": reason,
        "acceptance_gate_at": target.isoformat(),
        "seconds_until_acceptance_gate": max(0, int((target - now).total_seconds())),
    }


def parse_iso_datetime(value: str, fallback: datetime) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=fallback.tzinfo)
    return parsed


def read_latest_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path), "status": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "path": str(path), "status": "failed", "error": str(exc)}
    return {"exists": True, **payload}


def compact_refresh_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    compact = {
        "status": result.get("status") or result.get("health") or summary.get("status") or "",
        "ready": result.get("ready") if "ready" in result else None,
        "created_at": result.get("created_at") or result.get("started_at") or "",
        "json_path": result.get("json_path")
        or result.get("report_json_path")
        or result.get("latest_json_path")
        or "",
        "md_path": result.get("md_path")
        or result.get("report_md_path")
        or result.get("latest_md_path")
        or "",
        "latest_json_path": result.get("latest_json_path") or "",
        "latest_md_path": result.get("latest_md_path") or "",
    }
    if "with_manager_feedback_smoke" in result:
        compact["with_manager_feedback_smoke"] = result.get("with_manager_feedback_smoke") is True
    return compact


def build_evidence(
    runtime: dict[str, Any],
    semantic: dict[str, Any],
    mcp_smoke: dict[str, Any],
    reproducibility: dict[str, Any],
) -> list[dict[str, Any]]:
    runtime_summary = runtime.get("summary") if isinstance(runtime.get("summary"), dict) else {}
    semantic_readiness = semantic.get("readiness") if isinstance(semantic.get("readiness"), dict) else {}
    return [
        {
            "id": "runtime_health",
            "title": "Runtime health matrix",
            "status": str(runtime.get("status") or "missing"),
            "summary": runtime_health_summary(runtime_summary),
            "path": str(runtime.get("latest_md_path") or runtime.get("md_path") or runtime.get("path") or ""),
            "next_action": runtime_next_action(runtime),
        },
        {
            "id": "semantic_readiness",
            "title": "Semantic background readiness",
            "status": str(semantic.get("status") or "missing"),
            "summary": semantic_summary(semantic_readiness),
            "path": str(semantic.get("latest_md_path") or semantic.get("md_path") or semantic.get("path") or ""),
            "next_action": str(semantic.get("next_action") or ""),
        },
        {
            "id": "mcp_live_smoke",
            "title": "MCP live stdio smoke",
            "status": str(mcp_smoke.get("status") or "missing"),
            "summary": (
                f"tools={mcp_smoke.get('tools_total', 0)}, "
                f"read_source={mcp_smoke.get('read_source_status', '')}, "
                f"semantic={mcp_smoke.get('semantic_readiness_status', '')}"
            ),
            "path": str(mcp_smoke.get("latest_md_path") or mcp_smoke.get("md_path") or mcp_smoke.get("path") or ""),
            "next_action": "" if mcp_smoke.get("status") == "ok" else "Run agent-context mcp-live-smoke.",
        },
        {
            "id": "reproducibility_snapshot",
            "title": "Dirty worktree reproducibility snapshot",
            "status": str(reproducibility.get("status") or "missing"),
            "summary": reproducibility_summary(reproducibility),
            "path": str(reproducibility.get("latest_md_path") or reproducibility.get("path") or ""),
            "next_action": "" if reproducibility.get("status") == "ok" else "Run agent-context reproducibility-snapshot.",
        },
    ]


def runtime_health_summary(summary: dict[str, Any]) -> str:
    if not summary:
        return "latest runtime health report is missing"
    return (
        f"{summary.get('ok', 0)} ok / {summary.get('warning', 0)} warning / "
        f"{summary.get('failed', 0)} failed; warnings={summary.get('warning_checks') or []}"
    )


def runtime_next_action(runtime: dict[str, Any]) -> str:
    if runtime.get("status") in {"missing", "failed"}:
        return "Run agent-context runtime-health."
    checks = runtime.get("checks") if isinstance(runtime.get("checks"), list) else []
    actions = [str(item.get("next_action") or "") for item in checks if item.get("next_action")]
    return actions[0] if actions else ""


def semantic_summary(readiness: dict[str, Any]) -> str:
    if not readiness:
        return "latest semantic readiness report is missing"
    return (
        f"chunks={readiness.get('semantic_chunks', 0)}, "
        f"launchd={readiness.get('launchd_health', '')}, "
        f"trend={readiness.get('trend_status', '')}/{readiness.get('trend_confidence', '')}, "
        f"days={readiness.get('trend_days_observed', 0)}/{readiness.get('required_trend_days', 0)}, "
        f"unhealthy={readiness.get('trend_unhealthy_snapshots', 0)}"
    )


def reproducibility_summary(snapshot: dict[str, Any]) -> str:
    if not snapshot.get("exists"):
        return "latest reproducibility snapshot is missing"
    return (
        f"covered_roots={len(snapshot.get('covered_roots') or [])}/{snapshot.get('roots_total', 0)}, "
        f"stale={len(snapshot.get('stale_roots') or [])}, "
        f"missing={len(snapshot.get('missing_roots') or [])}"
    )


def acceptance_status(
    runtime: dict[str, Any],
    semantic: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> str:
    statuses = {item["id"]: str(item.get("status") or "") for item in evidence}
    if any(status in {"failed", "attention_required", "missing", "stale"} for status in statuses.values()):
        return "failed"
    runtime_summary = runtime.get("summary") if isinstance(runtime.get("summary"), dict) else {}
    runtime_only_waiting_for_semantic = (
        statuses.get("runtime_health") == "warning"
        and runtime_summary.get("failed", 0) == 0
        and list(runtime_summary.get("warning_checks") or []) == ["semantic_background"]
    )
    semantic_waiting = statuses.get("semantic_readiness") == "waiting_for_time"
    non_time_warnings = [
        item
        for item in evidence
        if item["status"] == "warning" and item["id"] != "runtime_health"
    ]
    if semantic_waiting and runtime_only_waiting_for_semantic and not non_time_warnings:
        return "waiting_for_time"
    if any(status == "warning" for status in statuses.values()) or semantic_waiting:
        return "warning"
    return "ok"


def acceptance_decision(status: str, semantic: dict[str, Any]) -> str:
    readiness = semantic.get("readiness") if isinstance(semantic.get("readiness"), dict) else {}
    if status == "ok":
        return "v1 acceptance evidence is complete."
    if status == "waiting_for_time":
        earliest = readiness.get("earliest_multi_day_check_after") or ""
        return (
            "Implementation evidence is present, but final v1 acceptance is time-gated by semantic background "
            f"multi-day trend evidence. Earliest recheck: {earliest}."
        )
    if status == "failed":
        return "v1 acceptance is blocked by missing, stale, or failed evidence."
    return "v1 acceptance has non-blocking warnings; inspect the evidence table before release."


def next_commands(
    out_root: Path,
    codex_plus_root: Path | None,
    *,
    with_manager_feedback_smoke: bool = False,
) -> list[str]:
    codex_arg = f" --codex-plus-root {codex_plus_root}" if codex_plus_root else ""
    manager_arg = " --with-manager-feedback-smoke" if with_manager_feedback_smoke else ""
    codex_smoke_arg = " --with-manager-feedback" if with_manager_feedback_smoke else ""
    root_arg = f"--out {out_root}"
    commands = [
        f"agent-context v1-refresh {root_arg}{codex_arg}{manager_arg}",
        f"agent-context v1-acceptance {root_arg}{codex_arg} --refresh-evidence{manager_arg}",
        f"agent-context semantic-launchd-monitor {root_arg} --with-launchctl",
        f"agent-context semantic-launchd-audit {root_arg}",
        f"agent-context semantic-launchd-trend {root_arg}",
        f"agent-context semantic-readiness {root_arg} --with-launchctl",
        f"agent-context runtime-health {root_arg}{codex_arg}",
        f"agent-context reproducibility-snapshot {root_arg}{codex_arg}",
        f"agent-context codex-plus-smoke {root_arg}{codex_arg}{codex_smoke_arg}" if codex_plus_root else "",
        f"agent-context mcp-live-smoke {root_arg}{codex_arg}{manager_arg}",
        f"agent-context v1-acceptance {root_arg}{codex_arg}",
    ]
    return [command for command in commands if command]


def render_v1_acceptance_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agent Context Runtime v1 Acceptance",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Ready: `{str(report.get('ready', False)).lower()}`",
        f"- Created at: `{report.get('created_at')}`",
        f"- Out root: `{report.get('out_root')}`",
        f"- Codex++ root: `{report.get('codex_plus_root')}`",
        f"- Decision: {report.get('decision')}",
        "",
        "## Evidence",
        "",
        "| Area | Status | Summary | Report | Next action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report.get("evidence") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md(str(item.get("title") or item.get("id") or "")),
                    f"`{item.get('status', '')}`",
                    escape_md(str(item.get("summary") or "")),
                    f"`{item.get('path', '')}`",
                    escape_md(str(item.get("next_action") or "")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Runtime Matrix",
            "",
            "| ID | Status | Requirement |",
            "| --- | --- | --- |",
        ]
    )
    for item in report.get("acceptance_matrix") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md(str(item.get("id") or "")),
                    f"`{item.get('status', '')}`",
                    escape_md(str(item.get("title") or "")),
                ]
            )
            + " |"
        )
    followup = report.get("followup_plan") if isinstance(report.get("followup_plan"), dict) else {}
    if followup:
        lines.extend(
            [
                "",
                "## Follow-Up Plan",
                "",
                f"- Follow-up report: `{followup.get('latest_md_path', '')}`",
                f"- Can recheck now: `{str(followup.get('can_recheck_now', False)).lower()}`",
                f"- Earliest recheck after: `{followup.get('earliest_recheck_after', '')}`",
                f"- Next monitor due: `{followup.get('next_monitor_due_at', '')}`",
                f"- Next evidence gate reason: `{followup.get('next_evidence_gate_reason', '')}`",
                f"- Next evidence gate at: `{followup.get('next_evidence_gate_at', '')}`",
                f"- Acceptance wait reason: `{followup.get('acceptance_wait_reason', '')}`",
                f"- Acceptance gate at: `{followup.get('acceptance_gate_at', '')}`",
                f"- Trend days remaining: `{followup.get('trend_days_remaining', '')}`",
            ]
        )
    lines.extend(["", "## Next Commands", ""])
    for command in report.get("next_commands") or []:
        lines.append(f"- `{command}`")
    lines.append("")
    return "\n".join(lines)


def render_v1_followup_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Agent Context Runtime v1 Follow-Up Plan",
        "",
        f"- Status: `{plan.get('status')}`",
        f"- Ready: `{str(plan.get('ready', False)).lower()}`",
        f"- Can recheck now: `{str(plan.get('can_recheck_now', False)).lower()}`",
        f"- Created at: `{plan.get('created_at')}`",
        f"- Earliest recheck after: `{plan.get('earliest_recheck_after')}`",
        f"- Next monitor due: `{plan.get('next_monitor_due_at')}`",
        f"- Next evidence gate reason: `{plan.get('next_evidence_gate_reason')}`",
        f"- Next evidence gate at: `{plan.get('next_evidence_gate_at')}`",
        f"- Acceptance wait reason: `{plan.get('acceptance_wait_reason')}`",
        f"- Acceptance gate at: `{plan.get('acceptance_gate_at')}`",
        f"- Trend days observed: `{plan.get('trend_days_observed')}`",
        f"- Trend days remaining: `{plan.get('trend_days_remaining')}`",
        f"- Reason: {plan.get('reason')}",
        "",
        "## Reports",
        "",
        f"- Acceptance: `{plan.get('acceptance_latest_md_path')}`",
        f"- Runtime health: `{plan.get('runtime_health_latest_md_path')}`",
        f"- Semantic readiness: `{plan.get('semantic_readiness_latest_md_path')}`",
        "",
        "## Commands",
        "",
    ]
    for command in plan.get("commands") or []:
        lines.append(f"- `{command}`")
    lines.append("")
    return "\n".join(lines)


def render_v1_followup_check_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agent Context Runtime v1 Follow-Up Check",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Action: `{report.get('action')}`",
        f"- Ready: `{str(report.get('ready', False)).lower()}`",
        f"- Can recheck now: `{str(report.get('can_recheck_now', False)).lower()}`",
        f"- Wait reason: `{report.get('wait_reason', '')}`",
        f"- Next gate at: `{report.get('next_gate_at', '')}`",
        f"- Seconds until next gate: `{report.get('seconds_until_next_gate', 0)}`",
        f"- Acceptance wait reason: `{report.get('acceptance_wait_reason', '')}`",
        f"- Acceptance gate at: `{report.get('acceptance_gate_at', '')}`",
        f"- Seconds until acceptance gate: `{report.get('seconds_until_acceptance_gate', 0)}`",
        f"- Created at: `{report.get('created_at')}`",
        f"- Earliest recheck after: `{report.get('earliest_recheck_after')}`",
        f"- Next monitor due: `{report.get('next_monitor_due_at')}`",
        f"- Trend days remaining: `{report.get('trend_days_remaining')}`",
        "",
        "## Reports",
        "",
        f"- Follow-up plan: `{report.get('followup_plan_latest_md_path')}`",
        f"- Acceptance: `{report.get('acceptance_latest_md_path')}`",
        "",
        "## Next Command",
        "",
        f"`{report.get('next_command') or ''}`",
        "",
    ]
    return "\n".join(lines)


def render_v1_stage_status_markdown(report: dict[str, Any]) -> str:
    gates = report.get("next_gates") if isinstance(report.get("next_gates"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Agent Context Runtime v1 Stage Status",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Ready: `{str(report.get('ready', False)).lower()}`",
        f"- Created at: `{report.get('created_at')}`",
        f"- Out root: `{report.get('out_root')}`",
        f"- Codex++ root: `{report.get('codex_plus_root')}`",
        f"- Decision: {report.get('decision') or ''}",
        f"- Stage counts: `{summary.get('status_counts', {})}`",
        "",
        "## Gates",
        "",
        f"- Wait reason: `{gates.get('wait_reason', '')}`",
        f"- Next evidence gate reason: `{gates.get('next_evidence_gate_reason') or gates.get('wait_reason', '')}`",
        f"- Next evidence gate: `{gates.get('next_evidence_gate_at') or gates.get('next_gate_at', '')}`",
        f"- Seconds until next evidence gate: `{gates.get('seconds_until_next_evidence_gate', gates.get('seconds_until_next_gate', 0))}`",
        f"- Acceptance wait reason: `{gates.get('acceptance_wait_reason', '')}`",
        f"- Acceptance gate: `{gates.get('acceptance_gate_at', '')}`",
        f"- Seconds until acceptance gate: `{gates.get('seconds_until_acceptance_gate', 0)}`",
        f"- Trend days remaining: `{gates.get('trend_days_remaining', '')}`",
        "",
        "## Stages",
        "",
        "| Stage | Status | Progress | Summary | Evidence | Next action |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for stage in report.get("stages") or []:
        evidence = "<br>".join(f"`{path}`" for path in (stage.get("evidence_paths") or [])[:4])
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md(str(stage.get("title") or stage.get("id") or "")),
                    f"`{stage.get('status', '')}`",
                    str(stage.get("progress", 0)),
                    escape_md(str(stage.get("summary") or "")),
                    evidence,
                    escape_md(str(stage.get("next_action") or "")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Source Reports", "", "| Report | Status | JSON | Markdown |", "| --- | --- | --- | --- |"])
    source_reports = report.get("source_reports") if isinstance(report.get("source_reports"), dict) else {}
    for name, source in source_reports.items():
        if not isinstance(source, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md(str(name)),
                    f"`{source.get('status', '')}`",
                    f"`{source.get('json_path', '')}`",
                    f"`{source.get('md_path', '')}`",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def render_v1_refresh_markdown(report: dict[str, Any]) -> str:
    semantic_evidence = report.get("semantic_evidence") if isinstance(report.get("semantic_evidence"), dict) else {}
    mcp_smoke = report.get("mcp_live_smoke") if isinstance(report.get("mcp_live_smoke"), dict) else {}
    runtime = report.get("runtime_health") if isinstance(report.get("runtime_health"), dict) else {}
    followup = report.get("followup_check") if isinstance(report.get("followup_check"), dict) else {}
    stage = report.get("stage_status") if isinstance(report.get("stage_status"), dict) else {}
    panel = report.get("panel") if isinstance(report.get("panel"), dict) else {}
    runtime_summary = runtime.get("summary") if isinstance(runtime.get("summary"), dict) else {}
    stage_summary = stage.get("summary") if isinstance(stage.get("summary"), dict) else {}
    gates = stage.get("next_gates") if isinstance(stage.get("next_gates"), dict) else {}
    lines = [
        "# Agent Context Runtime v1 Refresh",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Ready: `{str(report.get('ready', False)).lower()}`",
        f"- Created at: `{report.get('created_at')}`",
        f"- Out root: `{report.get('out_root')}`",
        f"- Codex++ root: `{report.get('codex_plus_root')}`",
        "",
        "## Semantic Evidence",
        "",
        f"- Refreshed: `{str(semantic_evidence.get('refreshed', False)).lower()}`",
        f"- Reason: `{semantic_evidence.get('reason', '')}`",
        f"- Consumed evidence gate: `{semantic_evidence.get('consumed_gate_at', '')}`",
        f"- Next evidence gate: `{semantic_evidence.get('next_gate_at', '')}`",
        f"- Seconds until next gate: `{semantic_evidence.get('seconds_until_next_gate', 0)}`",
        "",
        "## MCP Live Smoke",
        "",
        f"- Refreshed: `{str(mcp_smoke.get('refreshed', False)).lower()}`",
        f"- Status: `{mcp_smoke.get('status', '')}`",
        f"- Tools total: `{mcp_smoke.get('tools_total', 0)}`",
        f"- Missing required tools: `{mcp_smoke.get('required_tools_missing', [])}`",
        f"- Report: `{mcp_smoke.get('latest_md_path', '')}`",
        "",
        "## Runtime Health",
        "",
        f"- Refreshed: `{str(runtime.get('refreshed', False)).lower()}`",
        f"- Status: `{runtime.get('status', '')}`",
        f"- Summary: `{runtime_summary}`",
        f"- Report: `{runtime.get('latest_md_path', '')}`",
        "",
        "## Follow-Up Check",
        "",
        f"- Action: `{followup.get('action', '')}`",
        f"- Wait reason: `{followup.get('wait_reason', '')}`",
        f"- Next evidence gate: `{followup.get('next_gate_at', '')}`",
        f"- Acceptance gate: `{followup.get('acceptance_gate_at', '')}`",
        f"- Report: `{followup.get('latest_md_path', '')}`",
        "",
        "## Stage Status",
        "",
        f"- Stage counts: `{stage_summary.get('status_counts', {})}`",
        f"- Next evidence gate reason: `{gates.get('next_evidence_gate_reason') or gates.get('wait_reason', '')}`",
        f"- Next evidence gate: `{gates.get('next_evidence_gate_at') or gates.get('next_gate_at', '')}`",
        f"- Acceptance gate: `{gates.get('acceptance_gate_at', '')}`",
        f"- Report: `{stage.get('latest_md_path', '')}`",
        "",
        "## Panel",
        "",
        f"- Status JSON: `{panel.get('status_json_path', '')}`",
        f"- HTML: `{panel.get('html_path', '')}`",
        "",
    ]
    return "\n".join(lines)


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
