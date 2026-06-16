from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import json
import time

from .io import append_jsonl, ensure_dir, read_jsonl, write_text


LAUNCHD_VERSION = "0.1"
LAUNCHD_MONITOR_VERSION = "0.1"
LAUNCHD_AUDIT_VERSION = "0.1"
LAUNCHD_RECOVER_VERSION = "0.1"
LAUNCHD_TREND_VERSION = "0.1"
DEFAULT_LAUNCHD_LABEL = "com.gengrf.agent-context.semantic-maintenance"
DEFAULT_LAUNCHD_INTERVAL_MINUTES = 60
DEFAULT_LAUNCHD_SOURCE = "all"
DEFAULT_LAUNCHD_BUDGET = 32
DEFAULT_LAUNCHD_MAX_JOBS = 2
DEFAULT_LAUNCHD_MIN_INTERVAL_MINUTES = 30
DEFAULT_LAUNCHD_ANN_MAX_ENTRIES = 32
DEFAULT_LAUNCHD_ANN_MAX_BYTES = 1_000_000_000
DEFAULT_LAUNCHD_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DEFAULT_LAUNCHD_OVERDUE_GRACE_SECONDS = 300


def run_semantic_launchd(
    out_root: Path,
    *,
    action: str = "print",
    label: str = DEFAULT_LAUNCHD_LABEL,
    interval_minutes: int = DEFAULT_LAUNCHD_INTERVAL_MINUTES,
    source: str = DEFAULT_LAUNCHD_SOURCE,
    budget: int = DEFAULT_LAUNCHD_BUDGET,
    max_jobs: int = DEFAULT_LAUNCHD_MAX_JOBS,
    min_interval_minutes: int = DEFAULT_LAUNCHD_MIN_INTERVAL_MINUTES,
    ann_max_entries: int = DEFAULT_LAUNCHD_ANN_MAX_ENTRIES,
    ann_max_bytes: int = DEFAULT_LAUNCHD_ANN_MAX_BYTES,
    agent_context_bin: str = "agent-context",
    launch_agents_dir: Path | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    normalized_action = action if action in {"print", "install", "uninstall"} else "print"
    normalized = {
        "label": normalize_label(label),
        "interval_minutes": max(1, int(interval_minutes)),
        "source": source if source in {"downloads", "projects", "sessions", "all"} else DEFAULT_LAUNCHD_SOURCE,
        "budget": max(1, int(budget)),
        "max_jobs": max(1, int(max_jobs)),
        "min_interval_minutes": max(0, int(min_interval_minutes)),
        "ann_max_entries": max(1, int(ann_max_entries)),
        "ann_max_bytes": max(0, int(ann_max_bytes)),
        "agent_context_bin": agent_context_bin.strip() or "agent-context",
    }
    paths = launchd_paths(out_root, normalized["label"], launch_agents_dir)
    script_text = render_semantic_maintenance_script(out_root, normalized, paths)
    plist = render_launchd_plist(normalized["label"], paths, normalized["interval_minutes"])
    plist_xml = plistlib.dumps(plist, sort_keys=True).decode("utf-8")

    written = []
    removed = []
    if normalized_action == "install":
        ensure_dir(paths["script_path"].parent)
        ensure_dir(paths["stdout_path"].parent)
        write_text(paths["script_path"], script_text)
        paths["script_path"].chmod(0o755)
        ensure_dir(paths["plist_path"].parent)
        write_text(paths["plist_path"], plist_xml)
        written = [str(paths["script_path"]), str(paths["plist_path"])]
    elif normalized_action == "uninstall":
        for path in (paths["plist_path"], paths["script_path"]):
            if path.exists():
                path.unlink()
                removed.append(str(path))

    return {
        "launchd_version": LAUNCHD_VERSION,
        "action": normalized_action,
        "label": normalized["label"],
        "out_root": str(out_root),
        "plist_path": str(paths["plist_path"]),
        "script_path": str(paths["script_path"]),
        "stdout_path": str(paths["stdout_path"]),
        "stderr_path": str(paths["stderr_path"]),
        "written": written,
        "removed": removed,
        "plist": plist,
        "plist_xml": plist_xml,
        "script_text": script_text,
        "load_note": "Install writes files only. Use launchctl bootstrap/gui commands explicitly if you want to start it.",
        **normalized,
    }


def semantic_launchd_status(
    out_root: Path,
    *,
    label: str = DEFAULT_LAUNCHD_LABEL,
    launch_agents_dir: Path | None = None,
    tail_lines: int = 20,
    include_launchctl: bool = False,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    normalized_label = normalize_label(label)
    paths = launchd_paths(out_root, normalized_label, launch_agents_dir)
    issues: list[str] = []

    plist_status = inspect_launchd_plist(paths["plist_path"], normalized_label, paths, issues)
    script_status = inspect_launchd_script(paths["script_path"], issues)
    log_dir_exists = paths["stdout_path"].parent.exists()
    logs = {
        "stdout": inspect_launchd_log(paths["stdout_path"], tail_lines),
        "stderr": inspect_launchd_log(paths["stderr_path"], tail_lines),
    }
    reports = {
        "semantic_maintain": latest_launchd_report(out_root, "semantic-maintain-*.json"),
        "semantic_ann_prune": latest_launchd_report(out_root, "semantic-ann-prune-*.json"),
    }
    launchctl = inspect_launchctl(normalized_label) if include_launchctl else {"checked": False}

    plist_exists = bool(plist_status["exists"])
    script_exists = bool(script_status["exists"])
    if plist_exists != script_exists:
        issues.append("partial_installation")
    if (plist_exists or script_exists) and not log_dir_exists:
        issues.append("log_dir_missing")
    if include_launchctl and plist_exists and script_exists and not launchctl.get("loaded", False):
        issues.append("launchctl_not_loaded")

    installed = (
        plist_exists
        and script_exists
        and log_dir_exists
        and bool(plist_status["valid"])
        and bool(plist_status["label_matches"])
        and bool(plist_status["program_matches"])
        and bool(script_status["has_semantic_maintain"])
        and bool(script_status["has_semantic_ann_prune"])
    )
    if not plist_exists and not script_exists:
        health = "not_installed"
    elif installed and not issues:
        health = "ok"
    else:
        health = "degraded"

    return {
        "launchd_version": LAUNCHD_VERSION,
        "status_version": "0.1",
        "label": normalized_label,
        "out_root": str(out_root),
        "plist_path": str(paths["plist_path"]),
        "script_path": str(paths["script_path"]),
        "stdout_path": str(paths["stdout_path"]),
        "stderr_path": str(paths["stderr_path"]),
        "log_dir_path": str(paths["stdout_path"].parent),
        "log_dir_exists": log_dir_exists,
        "installed": installed,
        "health": health,
        "issues": sorted(set(issues)),
        "plist": plist_status,
        "script": script_status,
        "logs": logs,
        "reports": reports,
        "launchctl": launchctl,
        "load_note": "Status is read-only. It does not call launchctl or start/stop the LaunchAgent.",
    }


def run_semantic_launchd_monitor(
    out_root: Path,
    *,
    label: str = DEFAULT_LAUNCHD_LABEL,
    launch_agents_dir: Path | None = None,
    tail_lines: int = 20,
    with_launchctl: bool = True,
    max_history: int = 200,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    reports_dir = ensure_dir(out_root / "reports")
    status = semantic_launchd_status(
        out_root,
        label=label,
        launch_agents_dir=launch_agents_dir,
        tail_lines=tail_lines,
        include_launchctl=with_launchctl,
    )
    snapshot = semantic_launchd_monitor_snapshot(status)
    history_path = reports_dir / "semantic-launchd-monitor.jsonl"
    append_jsonl(history_path, snapshot)
    history = read_jsonl(history_path)[-max(1, int(max_history)) :]
    summary = summarize_launchd_monitor_history(history)
    latest_json_path = reports_dir / "semantic-launchd-monitor-latest.json"
    latest_md_path = reports_dir / "semantic-launchd-monitor-latest.md"
    result = {
        "semantic_launchd_monitor_version": LAUNCHD_MONITOR_VERSION,
        "status": "ok" if snapshot["health"] == "ok" else "degraded",
        "out_root": str(out_root),
        "history_path": str(history_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
        "snapshot": snapshot,
        "summary": summary,
    }
    write_text(latest_json_path, json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(latest_md_path, render_launchd_monitor_markdown(result))
    return result


def wait_for_semantic_launchd_run(
    out_root: Path,
    *,
    label: str = DEFAULT_LAUNCHD_LABEL,
    launch_agents_dir: Path | None = None,
    tail_lines: int = 20,
    with_launchctl: bool = True,
    max_history: int = 200,
    timeout_seconds: int = 7200,
    poll_seconds: int = 60,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    timeout_seconds = max(0, int(timeout_seconds))
    poll_seconds = max(1, int(poll_seconds))
    started_at = datetime.now().astimezone()
    run_id = f"semantic-launchd-wait-{started_at.strftime('%Y%m%d%H%M%S%f')}"
    reports_dir = ensure_dir(out_root / "reports")
    report_json_path = reports_dir / f"{run_id}.json"
    report_md_path = reports_dir / f"{run_id}.md"
    deadline = time.monotonic() + timeout_seconds

    initial = run_semantic_launchd_monitor(
        out_root,
        label=label,
        launch_agents_dir=launch_agents_dir,
        tail_lines=tail_lines,
        with_launchctl=with_launchctl,
        max_history=max_history,
    )
    initial_summary = initial.get("summary") if isinstance(initial.get("summary"), dict) else {}
    initial_runs = initial_summary.get("latest_runs")
    initial_activity = str(initial_summary.get("latest_launchd_activity_at") or "")
    snapshots = [compact_wait_snapshot(initial)]
    latest = initial
    status = "timeout"
    stop_reason = "timeout"

    while time.monotonic() < deadline:
        summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
        wait_seconds = poll_seconds
        seconds_until_due = summary.get("seconds_until_next_expected_run")
        if isinstance(seconds_until_due, int) and seconds_until_due > 0:
            wait_seconds = min(wait_seconds, seconds_until_due + 1)
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            break
        time.sleep(min(wait_seconds, remaining))
        latest = run_semantic_launchd_monitor(
            out_root,
            label=label,
            launch_agents_dir=launch_agents_dir,
            tail_lines=tail_lines,
            with_launchctl=with_launchctl,
            max_history=max_history,
        )
        snapshots.append(compact_wait_snapshot(latest))
        changed, reason = launchd_run_advanced(initial_runs, initial_activity, latest)
        if changed:
            status = "ok"
            stop_reason = reason
            break

    result = {
        "semantic_launchd_wait_version": "0.1",
        "run_id": run_id,
        "status": status,
        "stop_reason": stop_reason,
        "out_root": str(out_root),
        "started_at": started_at.isoformat(),
        "timeout_seconds": timeout_seconds,
        "poll_seconds": poll_seconds,
        "initial_runs": initial_runs,
        "initial_launchd_activity_at": initial_activity,
        "latest_summary": latest.get("summary") if isinstance(latest.get("summary"), dict) else {},
        "snapshots": snapshots,
        "report_json_path": str(report_json_path),
        "report_md_path": str(report_md_path),
    }
    write_text(report_json_path, json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(report_md_path, render_launchd_wait_markdown(result))
    return result


def run_semantic_launchd_audit(
    out_root: Path,
    *,
    max_history: int = 200,
    min_snapshots: int = 2,
    consecutive_unhealthy_threshold: int = 3,
    max_snapshot_age_seconds: int | None = None,
    notify: bool = False,
    notify_on: str = "alert",
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    started_at = datetime.now().astimezone()
    run_id = f"semantic-launchd-audit-{started_at.strftime('%Y%m%d%H%M%S%f')}"
    reports_dir = ensure_dir(out_root / "reports")
    history_path = reports_dir / "semantic-launchd-monitor.jsonl"
    report_json_path = reports_dir / f"{run_id}.json"
    report_md_path = reports_dir / f"{run_id}.md"
    latest_json_path = reports_dir / "semantic-launchd-audit-latest.json"
    latest_md_path = reports_dir / "semantic-launchd-audit-latest.md"
    history = read_jsonl(history_path)[-max(1, int(max_history)) :]
    summary = summarize_launchd_monitor_history(history)
    audit = audit_launchd_monitor_history(
        history,
        summary,
        min_snapshots=max(1, int(min_snapshots)),
        consecutive_unhealthy_threshold=max(1, int(consecutive_unhealthy_threshold)),
        max_snapshot_age_seconds=max_snapshot_age_seconds,
    )
    result = {
        "semantic_launchd_audit_version": LAUNCHD_AUDIT_VERSION,
        "run_id": run_id,
        "status": audit["health"],
        "health": audit["health"],
        "out_root": str(out_root),
        "started_at": started_at.isoformat(),
        "history_path": str(history_path),
        "report_json_path": str(report_json_path),
        "report_md_path": str(report_md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
        "policy": audit["policy"],
        "summary": summary,
        "metrics": audit["metrics"],
        "alerts": audit["alerts"],
        "recommendations": audit["recommendations"],
    }
    result["notification"] = maybe_notify_launchd_audit(result, requested=notify, notify_on=notify_on)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(report_json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_launchd_audit_markdown(result)
    write_text(report_md_path, markdown)
    write_text(latest_md_path, markdown)
    return result


def run_semantic_launchd_recover(
    out_root: Path,
    *,
    apply: bool = False,
    verify_after_apply: bool = False,
    label: str = DEFAULT_LAUNCHD_LABEL,
    launch_agents_dir: Path | None = None,
    max_history: int = 200,
    agent_context_bin: str = "agent-context",
    interval_minutes: int = DEFAULT_LAUNCHD_INTERVAL_MINUTES,
    source: str = DEFAULT_LAUNCHD_SOURCE,
    budget: int = DEFAULT_LAUNCHD_BUDGET,
    max_jobs: int = DEFAULT_LAUNCHD_MAX_JOBS,
    min_interval_minutes: int = DEFAULT_LAUNCHD_MIN_INTERVAL_MINUTES,
    ann_max_entries: int = DEFAULT_LAUNCHD_ANN_MAX_ENTRIES,
    ann_max_bytes: int = DEFAULT_LAUNCHD_ANN_MAX_BYTES,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    normalized_label = normalize_label(label)
    started_at = datetime.now().astimezone()
    run_id = f"semantic-launchd-recover-{started_at.strftime('%Y%m%d%H%M%S%f')}"
    reports_dir = ensure_dir(out_root / "reports")
    report_json_path = reports_dir / f"{run_id}.json"
    report_md_path = reports_dir / f"{run_id}.md"
    latest_json_path = reports_dir / "semantic-launchd-recover-latest.json"
    latest_md_path = reports_dir / "semantic-launchd-recover-latest.md"
    status = semantic_launchd_status(
        out_root,
        label=normalized_label,
        launch_agents_dir=launch_agents_dir,
        tail_lines=40,
        include_launchctl=True,
    )
    audit = run_semantic_launchd_audit(out_root, max_history=max_history)
    config = {
        "label": normalized_label,
        "interval_minutes": max(1, int(interval_minutes)),
        "source": source if source in {"downloads", "projects", "sessions", "all"} else DEFAULT_LAUNCHD_SOURCE,
        "budget": max(1, int(budget)),
        "max_jobs": max(1, int(max_jobs)),
        "min_interval_minutes": max(0, int(min_interval_minutes)),
        "ann_max_entries": max(1, int(ann_max_entries)),
        "ann_max_bytes": max(0, int(ann_max_bytes)),
        "agent_context_bin": agent_context_bin.strip() or "agent-context",
    }
    actions = plan_semantic_launchd_recovery(out_root, status, audit, launch_agents_dir=launch_agents_dir, config=config)
    executed_actions = [execute_launchd_recovery_action(action, out_root, launch_agents_dir=launch_agents_dir, config=config) for action in actions] if apply else actions
    failed_actions = [action for action in executed_actions if action.get("status") == "failed"]
    verification = (
        verify_semantic_launchd_recovery(
            out_root,
            pre_status=status,
            actions=executed_actions,
            label=normalized_label,
            launch_agents_dir=launch_agents_dir,
            max_history=max_history,
        )
        if apply and verify_after_apply
        else {"requested": bool(verify_after_apply), "status": "skipped", "passed": None, "reason": "apply_required" if verify_after_apply else "not_requested"}
    )
    if failed_actions:
        recovery_status = "failed"
    elif verification.get("status") == "failed":
        recovery_status = "verification_failed"
    elif not actions:
        recovery_status = "no_action"
    elif apply:
        recovery_status = "applied"
    else:
        recovery_status = "planned"
    result = {
        "semantic_launchd_recover_version": LAUNCHD_RECOVER_VERSION,
        "run_id": run_id,
        "status": recovery_status,
        "dry_run": not apply,
        "out_root": str(out_root),
        "started_at": started_at.isoformat(),
        "report_json_path": str(report_json_path),
        "report_md_path": str(report_md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
        "status_summary": {
            "health": status.get("health", ""),
            "installed": status.get("installed", False),
            "issues": status.get("issues") or [],
            "launchctl": status.get("launchctl") if isinstance(status.get("launchctl"), dict) else {},
        },
        "audit_summary": {
            "health": audit.get("health", ""),
            "alerts": [alert.get("code") for alert in audit.get("alerts") or [] if isinstance(alert, dict)],
            "report_json_path": audit.get("report_json_path", ""),
        },
        "actions": executed_actions,
        "action_count": len(actions),
        "failed_action_count": len(failed_actions),
        "verification": verification,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(report_json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_launchd_recover_markdown(result)
    write_text(report_md_path, markdown)
    write_text(latest_md_path, markdown)
    return result


def verify_semantic_launchd_recovery(
    out_root: Path,
    *,
    pre_status: dict[str, Any],
    actions: list[dict[str, Any]],
    label: str,
    launch_agents_dir: Path | None,
    max_history: int,
) -> dict[str, Any]:
    failed_actions = [action for action in actions if action.get("status") == "failed"]
    if failed_actions:
        return {
            "requested": True,
            "status": "failed",
            "passed": False,
            "reason": "actions_failed",
            "failed_action_ids": [action.get("id") for action in failed_actions],
        }
    status = semantic_launchd_status(
        out_root,
        label=label,
        launch_agents_dir=launch_agents_dir,
        tail_lines=40,
        include_launchctl=True,
    )
    monitor = run_semantic_launchd_monitor(
        out_root,
        label=label,
        launch_agents_dir=launch_agents_dir,
        tail_lines=40,
        with_launchctl=True,
        max_history=max_history,
    )
    audit = run_semantic_launchd_audit(out_root, max_history=max_history)
    action_ids = {str(action.get("id") or "") for action in actions}
    checks: list[dict[str, Any]] = []
    if "install_files" in action_ids:
        checks.append({"name": "installed", "passed": bool(status.get("installed", False))})
    if "bootstrap" in action_ids:
        launchctl = status.get("launchctl") if isinstance(status.get("launchctl"), dict) else {}
        checks.append({"name": "launchctl_loaded", "passed": bool(launchctl.get("loaded", False))})
    if "kickstart" in action_ids:
        pre_launchctl = pre_status.get("launchctl") if isinstance(pre_status.get("launchctl"), dict) else {}
        post_summary = monitor.get("summary") if isinstance(monitor.get("summary"), dict) else {}
        pre_runs = pre_launchctl.get("runs")
        post_runs = post_summary.get("latest_runs")
        runs_advanced = isinstance(pre_runs, int) and isinstance(post_runs, int) and post_runs > pre_runs
        checks.append({"name": "kickstart_runs_advanced", "passed": runs_advanced, "pre_runs": pre_runs, "post_runs": post_runs})
    if "monitor" in action_ids:
        checks.append({"name": "monitor_snapshot_written", "passed": bool(monitor.get("latest_json_path"))})
    checks.append({"name": "audit_not_alert", "passed": audit.get("health") != "alert", "audit_health": audit.get("health", "")})
    passed = all(bool(check.get("passed", False)) for check in checks)
    return {
        "requested": True,
        "status": "ok" if passed else "failed",
        "passed": passed,
        "reason": "" if passed else "verification_checks_failed",
        "checks": checks,
        "status_health": status.get("health", ""),
        "monitor_path": monitor.get("latest_json_path", ""),
        "audit_health": audit.get("health", ""),
        "audit_report_json_path": audit.get("report_json_path", ""),
    }


def plan_semantic_launchd_recovery(
    out_root: Path,
    status: dict[str, Any],
    audit: dict[str, Any],
    *,
    launch_agents_dir: Path | None,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    paths = launchd_paths(out_root, config["label"], launch_agents_dir)
    launchctl = status.get("launchctl") if isinstance(status.get("launchctl"), dict) else {}
    issues = set(status.get("issues") or [])
    actions: list[dict[str, Any]] = []
    install_needed = status.get("health") == "not_installed" or bool(
        issues
        & {
            "partial_installation",
            "log_dir_missing",
            "plist_unreadable",
            "plist_invalid",
            "plist_label_mismatch",
            "plist_program_mismatch",
            "plist_stdout_mismatch",
            "plist_stderr_mismatch",
            "script_unreadable",
            "script_not_executable",
            "script_missing_semantic_maintain",
            "script_missing_semantic_ann_prune",
        }
    )
    if install_needed:
        actions.append(
            recovery_action(
                "install_files",
                "LaunchAgent plist/script/log directory are missing or degraded.",
                ["agent-context", "semantic-launchd", "--out", str(out_root), "--install", "--label", config["label"]],
            )
        )
    loaded = bool(launchctl.get("loaded", False))
    if install_needed or (status.get("installed") and launchctl.get("checked") and not loaded):
        actions.append(
            recovery_action(
                "bootstrap",
                "LaunchAgent should be loaded after install or because launchctl reports it is not loaded.",
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(paths["plist_path"])],
            )
        )
    alert_codes = {str(alert.get("code") or "") for alert in audit.get("alerts") or [] if isinstance(alert, dict)}
    kickstart_codes = {
        "latest_snapshot_stale",
        "natural_run_overdue",
        "last_exit_nonzero",
        "maintain_status_failed",
        "prune_status_failed",
        "missing_expected_runs",
    }
    if loaded and alert_codes & kickstart_codes:
        actions.append(
            recovery_action(
                "kickstart",
                "LaunchAgent is loaded but audit found stale, overdue, failed, or missing runs.",
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{config['label']}"],
            )
        )
    if loaded and not alert_codes and audit.get("health") == "warning":
        actions.append(
            recovery_action(
                "monitor",
                "Audit only has warning-level history gaps; collect a fresh monitor snapshot.",
                ["agent-context", "semantic-launchd-monitor", "--out", str(out_root), "--label", config["label"], "--with-launchctl"],
            )
        )
    return actions


def recovery_action(action_id: str, reason: str, command: list[str]) -> dict[str, Any]:
    return {
        "id": action_id,
        "reason": reason,
        "command": command,
        "status": "planned",
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "error": "",
    }


def execute_launchd_recovery_action(
    action: dict[str, Any],
    out_root: Path,
    *,
    launch_agents_dir: Path | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    executed = dict(action)
    action_id = str(action.get("id") or "")
    try:
        if action_id == "install_files":
            installed = run_semantic_launchd(
                out_root,
                action="install",
                label=config["label"],
                interval_minutes=config["interval_minutes"],
                source=config["source"],
                budget=config["budget"],
                max_jobs=config["max_jobs"],
                min_interval_minutes=config["min_interval_minutes"],
                ann_max_entries=config["ann_max_entries"],
                ann_max_bytes=config["ann_max_bytes"],
                agent_context_bin=config["agent_context_bin"],
                launch_agents_dir=launch_agents_dir,
            )
            executed["status"] = "applied"
            executed["result"] = {"written": installed.get("written", [])}
            return executed
        if action_id == "monitor":
            monitored = run_semantic_launchd_monitor(
                out_root,
                label=config["label"],
                launch_agents_dir=launch_agents_dir,
                with_launchctl=True,
            )
            executed["status"] = "applied"
            executed["result"] = {"latest_json_path": monitored.get("latest_json_path", "")}
            return executed
        completed = subprocess.run(
            list(action.get("command") or []),
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        executed["status"] = "failed"
        executed["error"] = str(exc)
        return executed
    executed["returncode"] = completed.returncode
    executed["stdout"] = (completed.stdout or "").strip()
    executed["stderr"] = (completed.stderr or "").strip()
    executed["status"] = "applied" if completed.returncode == 0 else "failed"
    return executed


def render_launchd_recover_markdown(result: dict[str, Any]) -> str:
    action_lines = [
        f"- `{action.get('status', '')}` `{action.get('id', '')}`: `{shlex.join([str(part) for part in action.get('command') or []])}`"
        for action in result.get("actions") or []
    ] or ["- none"]
    return "\n".join(
        [
            "# Semantic LaunchAgent Recovery",
            "",
            f"- Status: `{result.get('status', '')}`",
            f"- Dry run: `{result.get('dry_run', True)}`",
            f"- Status health: `{(result.get('status_summary') or {}).get('health', '')}`",
            f"- Audit health: `{(result.get('audit_summary') or {}).get('health', '')}`",
            f"- Action count: `{result.get('action_count', 0)}`",
            f"- Failed action count: `{result.get('failed_action_count', 0)}`",
            "",
            "## Actions",
            "",
            *action_lines,
            "",
        ]
    )


def latest_semantic_launchd_recover(out_root: Path) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    latest_json_path = out_root / "reports" / "semantic-launchd-recover-latest.json"
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "summary": {},
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "error": str(exc),
            "summary": {},
        }
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "status": data.get("status", ""),
        "summary": {
            "status": data.get("status", ""),
            "dry_run": data.get("dry_run", True),
            "action_count": data.get("action_count", 0),
            "failed_action_count": data.get("failed_action_count", 0),
            "run_id": data.get("run_id", ""),
            "started_at": data.get("started_at", ""),
        },
    }


def run_semantic_launchd_trend(
    out_root: Path,
    *,
    max_history: int = 1000,
    min_days: int = 2,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    started_at = datetime.now().astimezone()
    run_id = f"semantic-launchd-trend-{started_at.strftime('%Y%m%d%H%M%S%f')}"
    reports_dir = ensure_dir(out_root / "reports")
    history_path = reports_dir / "semantic-launchd-monitor.jsonl"
    report_json_path = reports_dir / f"{run_id}.json"
    report_md_path = reports_dir / f"{run_id}.md"
    latest_json_path = reports_dir / "semantic-launchd-trend-latest.json"
    latest_md_path = reports_dir / "semantic-launchd-trend-latest.md"
    history = read_jsonl(history_path)[-max(1, int(max_history)) :]
    trend = summarize_launchd_trend(history, min_days=max(1, int(min_days)))
    result = {
        "semantic_launchd_trend_version": LAUNCHD_TREND_VERSION,
        "run_id": run_id,
        "status": trend["status"],
        "out_root": str(out_root),
        "started_at": started_at.isoformat(),
        "history_path": str(history_path),
        "report_json_path": str(report_json_path),
        "report_md_path": str(report_md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
        **trend,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(report_json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_launchd_trend_markdown(result)
    write_text(report_md_path, markdown)
    write_text(latest_md_path, markdown)
    return result


def summarize_launchd_trend(history: list[dict[str, Any]], *, min_days: int) -> dict[str, Any]:
    if not history:
        return {
            "status": "missing",
            "confidence": "missing",
            "metrics": {
                "snapshots": 0,
                "days_observed": 0,
                "observed_window_seconds": 0,
                "runs_delta": 0,
                "unhealthy_snapshots": 0,
                "unhealthy_rate": 0.0,
            },
            "daily": [],
            "hourly": [],
            "limitations": ["No semantic launchd monitor history exists yet."],
        }
    sorted_history = sorted(history, key=lambda item: str(item.get("created_at") or ""))
    summary = summarize_launchd_monitor_history(sorted_history)
    daily = bucket_launchd_history(sorted_history, granularity="day")
    hourly = bucket_launchd_history(sorted_history, granularity="hour")
    snapshots = len(sorted_history)
    unhealthy = sum(1 for item in sorted_history if item.get("health") != "ok")
    days_observed = len(daily)
    observed_window_seconds = monitor_observed_window_seconds(sorted_history[0], sorted_history[-1])
    limitations: list[str] = []
    if days_observed < min_days:
        limitations.append(f"Only {days_observed} day(s) observed; need {min_days} for multi-day stability.")
    confidence = "multi_day" if days_observed >= min_days else "short_window"
    if unhealthy:
        status = "degraded"
    elif confidence == "multi_day":
        status = "ok"
    else:
        status = "short_window"
    return {
        "status": status,
        "confidence": confidence,
        "metrics": {
            "snapshots": snapshots,
            "days_observed": days_observed,
            "observed_window_seconds": observed_window_seconds,
            "runs_delta": summary.get("runs_delta", 0),
            "unhealthy_snapshots": unhealthy,
            "unhealthy_rate": round(unhealthy / snapshots, 4) if snapshots else 0.0,
            "latest_runs": summary.get("latest_runs"),
            "latest_health": summary.get("latest_health", ""),
            "latest_launchd_activity_at": summary.get("latest_launchd_activity_at", ""),
        },
        "daily": daily,
        "hourly": hourly,
        "limitations": limitations,
    }


def bucket_launchd_history(history: list[dict[str, Any]], *, granularity: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in history:
        created_at = parse_iso_datetime(str(item.get("created_at") or ""))
        if not created_at:
            continue
        if granularity == "hour":
            key = created_at.strftime("%Y-%m-%dT%H:00")
        else:
            key = created_at.strftime("%Y-%m-%d")
        buckets.setdefault(key, []).append(item)
    return [summarize_launchd_trend_bucket(key, items) for key, items in sorted(buckets.items())]


def summarize_launchd_trend_bucket(key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    first = items[0]
    latest = items[-1]
    first_launchctl = first.get("launchctl") if isinstance(first.get("launchctl"), dict) else {}
    latest_launchctl = latest.get("launchctl") if isinstance(latest.get("launchctl"), dict) else {}
    first_runs = first_launchctl.get("runs")
    latest_runs = latest_launchctl.get("runs")
    runs_delta = (latest_runs - first_runs) if isinstance(first_runs, int) and isinstance(latest_runs, int) else 0
    unhealthy = sum(1 for item in items if item.get("health") != "ok")
    maintain_statuses: dict[str, int] = {}
    prune_statuses: dict[str, int] = {}
    for item in items:
        reports = item.get("reports") if isinstance(item.get("reports"), dict) else {}
        maintain = ((reports.get("semantic_maintain") or {}).get("summary") or {}).get("status", "")
        prune = ((reports.get("semantic_ann_prune") or {}).get("summary") or {}).get("status", "")
        if maintain:
            maintain_statuses[str(maintain)] = maintain_statuses.get(str(maintain), 0) + 1
        if prune:
            prune_statuses[str(prune)] = prune_statuses.get(str(prune), 0) + 1
    return {
        "bucket": key,
        "snapshots": len(items),
        "first_created_at": first.get("created_at", ""),
        "latest_created_at": latest.get("created_at", ""),
        "first_runs": first_runs,
        "latest_runs": latest_runs,
        "runs_delta": runs_delta,
        "unhealthy_snapshots": unhealthy,
        "latest_health": latest.get("health", ""),
        "maintain_statuses": maintain_statuses,
        "prune_statuses": prune_statuses,
    }


def render_launchd_trend_markdown(result: dict[str, Any]) -> str:
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    limitations = result.get("limitations") if isinstance(result.get("limitations"), list) else []
    daily = result.get("daily") if isinstance(result.get("daily"), list) else []
    daily_lines = [
        f"- `{item.get('bucket', '')}` snapshots `{item.get('snapshots', 0)}`, runs delta `{item.get('runs_delta', 0)}`, unhealthy `{item.get('unhealthy_snapshots', 0)}`"
        for item in daily
    ] or ["- none"]
    limitation_lines = [f"- {item}" for item in limitations] or ["- none"]
    return "\n".join(
        [
            "# Semantic LaunchAgent Trend",
            "",
            f"- Status: `{result.get('status', '')}`",
            f"- Confidence: `{result.get('confidence', '')}`",
            f"- Snapshots: `{metrics.get('snapshots', 0)}`",
            f"- Days observed: `{metrics.get('days_observed', 0)}`",
            f"- Runs delta: `{metrics.get('runs_delta', 0)}`",
            f"- Unhealthy snapshots: `{metrics.get('unhealthy_snapshots', 0)}`",
            "",
            "## Daily Buckets",
            "",
            *daily_lines,
            "",
            "## Limitations",
            "",
            *limitation_lines,
            "",
        ]
    )


def latest_semantic_launchd_trend(out_root: Path) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    latest_json_path = out_root / "reports" / "semantic-launchd-trend-latest.json"
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "summary": {},
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "error": str(exc),
            "summary": {},
        }
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "status": data.get("status", ""),
        "confidence": data.get("confidence", ""),
        "summary": {
            "status": data.get("status", ""),
            "confidence": data.get("confidence", ""),
            "snapshots": metrics.get("snapshots", 0),
            "days_observed": metrics.get("days_observed", 0),
            "runs_delta": metrics.get("runs_delta", 0),
            "unhealthy_snapshots": metrics.get("unhealthy_snapshots", 0),
            "run_id": data.get("run_id", ""),
            "started_at": data.get("started_at", ""),
        },
    }


def maybe_notify_launchd_audit(result: dict[str, Any], *, requested: bool, notify_on: str = "alert") -> dict[str, Any]:
    normalized_notify_on = notify_on if notify_on in {"alert", "warning", "always"} else "alert"
    notification = {
        "requested": bool(requested),
        "notify_on": normalized_notify_on,
        "sent": False,
        "skipped_reason": "",
        "returncode": None,
        "error": "",
    }
    if not requested:
        notification["skipped_reason"] = "not_requested"
        return notification
    health = str(result.get("health") or "")
    if not launchd_audit_should_notify(health, normalized_notify_on):
        notification["skipped_reason"] = "health_below_threshold"
        return notification
    title, subtitle, body = launchd_audit_notification_text(result)
    try:
        completed = subprocess.run(
            [
                "osascript",
                "-e",
                f"display notification {applescript_quote(body)} with title {applescript_quote(title)} subtitle {applescript_quote(subtitle)}",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        notification["error"] = str(exc)
        return notification
    notification["returncode"] = completed.returncode
    if completed.returncode == 0:
        notification["sent"] = True
    else:
        notification["error"] = (completed.stderr or completed.stdout or "").strip()
    return notification


def launchd_audit_should_notify(health: str, notify_on: str) -> bool:
    if notify_on == "always":
        return True
    if notify_on == "warning":
        return health in {"warning", "alert"}
    return health == "alert"


def launchd_audit_notification_text(result: dict[str, Any]) -> tuple[str, str, str]:
    health = str(result.get("health") or "unknown")
    alerts = result.get("alerts") if isinstance(result.get("alerts"), list) else []
    codes = [str(alert.get("code") or "") for alert in alerts[:3] if isinstance(alert, dict)]
    title = "Agent Context Semantic Audit"
    subtitle = health.upper()
    report_path = str(result.get("report_md_path") or result.get("report_json_path") or "")
    code_text = ", ".join(code for code in codes if code) or "no alert code"
    body = f"{len(alerts)} alert(s): {code_text}. Report: {report_path}"
    return title, subtitle, body[:240]


def applescript_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def audit_launchd_monitor_history(
    history: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    min_snapshots: int,
    consecutive_unhealthy_threshold: int,
    max_snapshot_age_seconds: int | None,
) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []

    def add_alert(code: str, severity: str, message: str, evidence: Any = None) -> None:
        alert = {"code": code, "severity": severity, "message": message}
        if evidence is not None:
            alert["evidence"] = evidence
        alerts.append(alert)

    if not history:
        add_alert("monitor_history_missing", "warning", "No semantic launchd monitor history exists yet.")
        return {
            "health": "warning",
            "alerts": alerts,
            "metrics": {
                "snapshots": 0,
                "consecutive_unhealthy_snapshots": 0,
                "observed_window_seconds": 0,
                "expected_runs_in_window": 0,
                "runs_delta": 0,
            },
            "policy": {
                "min_snapshots": min_snapshots,
                "consecutive_unhealthy_threshold": consecutive_unhealthy_threshold,
                "max_snapshot_age_seconds": max_snapshot_age_seconds,
            },
            "recommendations": ["Run semantic-launchd-monitor or wait for the LaunchAgent script to write its first snapshot."],
        }

    first = history[0]
    latest = history[-1]
    latest_launchctl = latest.get("launchctl") if isinstance(latest.get("launchctl"), dict) else {}
    latest_logs = latest.get("logs") if isinstance(latest.get("logs"), dict) else {}
    run_interval_seconds = latest_launchctl.get("run_interval_seconds")
    derived_max_age = (
        max_snapshot_age_seconds
        if isinstance(max_snapshot_age_seconds, int) and max_snapshot_age_seconds > 0
        else ((run_interval_seconds * 2) + DEFAULT_LAUNCHD_OVERDUE_GRACE_SECONDS if isinstance(run_interval_seconds, int) and run_interval_seconds > 0 else 7200)
    )
    consecutive_unhealthy = count_consecutive_unhealthy(history)
    observed_window_seconds = monitor_observed_window_seconds(first, latest)
    expected_runs = expected_launchd_runs_in_window(observed_window_seconds, run_interval_seconds)
    runs_delta = int(summary.get("runs_delta") or 0)

    if len(history) < min_snapshots:
        add_alert(
            "insufficient_monitor_history",
            "warning",
            "Monitor history is too short to judge stability.",
            {"snapshots": len(history), "min_snapshots": min_snapshots},
        )
    latest_snapshot_age = summary.get("latest_snapshot_age_seconds")
    if isinstance(latest_snapshot_age, int) and latest_snapshot_age > derived_max_age:
        add_alert(
            "latest_snapshot_stale",
            "alert",
            "Latest monitor snapshot is older than the allowed threshold.",
            {"latest_snapshot_age_seconds": latest_snapshot_age, "max_snapshot_age_seconds": derived_max_age},
        )
    latest_health = str(summary.get("latest_health") or "unknown")
    if latest_health != "ok":
        add_alert("latest_health_not_ok", "alert", "Latest monitor health is not ok.", {"latest_health": latest_health})
    if bool(latest.get("installed", False)) and bool(latest_launchctl.get("checked", False)) and not bool(latest_launchctl.get("loaded", False)):
        add_alert("launchd_not_loaded", "alert", "LaunchAgent is installed but launchctl does not report it as loaded.")
    latest_exit = str(latest_launchctl.get("last_exit_code") or "")
    if latest_exit and latest_exit != "0":
        add_alert("last_exit_nonzero", "alert", "Latest launchd run exited with a non-zero code.", {"last_exit_code": latest_exit})
    if bool(summary.get("natural_run_overdue", False)):
        add_alert(
            "natural_run_overdue",
            "alert",
            "The next natural launchd cycle is overdue beyond the grace window.",
            {"seconds_overdue": summary.get("seconds_overdue"), "overdue_grace_seconds": summary.get("overdue_grace_seconds")},
        )
    maintain_status = str(summary.get("latest_maintain_status") or "")
    if maintain_status and maintain_status not in {"ok", "skipped"}:
        add_alert("maintain_status_failed", "alert", "Latest semantic-maintain report is not ok/skipped.", {"status": maintain_status})
    elif not maintain_status:
        add_alert("maintain_status_missing", "warning", "No semantic-maintain status is present in the latest monitor snapshot.")
    prune_status = str(summary.get("latest_prune_status") or "")
    if prune_status and prune_status != "ok":
        add_alert("prune_status_failed", "alert", "Latest semantic-ann-prune report is not ok.", {"status": prune_status})
    elif not prune_status:
        add_alert("prune_status_missing", "warning", "No semantic-ann-prune status is present in the latest monitor snapshot.")
    stderr_size = int(latest_logs.get("stderr_size_bytes") or 0)
    if stderr_size > 0:
        add_alert("stderr_has_output", "warning", "LaunchAgent stderr log is not empty.", {"stderr_size_bytes": stderr_size})
    if consecutive_unhealthy >= consecutive_unhealthy_threshold:
        add_alert(
            "consecutive_unhealthy_snapshots",
            "alert",
            "Monitor history has too many consecutive unhealthy snapshots.",
            {"consecutive_unhealthy_snapshots": consecutive_unhealthy, "threshold": consecutive_unhealthy_threshold},
        )
    if expected_runs > 0 and runs_delta < expected_runs and bool(summary.get("natural_run_overdue", False)):
        add_alert(
            "missing_expected_runs",
            "alert",
            "Observed launchd runs are below the expected count for the monitor window.",
            {"runs_delta": runs_delta, "expected_runs_in_window": expected_runs, "observed_window_seconds": observed_window_seconds},
        )

    severities = {alert["severity"] for alert in alerts}
    health = "alert" if "alert" in severities else "warning" if "warning" in severities else "ok"
    return {
        "health": health,
        "alerts": alerts,
        "metrics": {
            "snapshots": len(history),
            "consecutive_unhealthy_snapshots": consecutive_unhealthy,
            "observed_window_seconds": observed_window_seconds,
            "expected_runs_in_window": expected_runs,
            "runs_delta": runs_delta,
            "latest_snapshot_age_seconds": latest_snapshot_age,
            "max_snapshot_age_seconds": derived_max_age,
        },
        "policy": {
            "min_snapshots": min_snapshots,
            "consecutive_unhealthy_threshold": consecutive_unhealthy_threshold,
            "max_snapshot_age_seconds": derived_max_age,
        },
        "recommendations": launchd_audit_recommendations(alerts),
    }


def count_consecutive_unhealthy(history: list[dict[str, Any]]) -> int:
    count = 0
    for item in reversed(history):
        if item.get("health") == "ok":
            break
        count += 1
    return count


def monitor_observed_window_seconds(first: dict[str, Any], latest: dict[str, Any]) -> int:
    first_at = parse_iso_datetime(str(first.get("created_at") or ""))
    latest_at = parse_iso_datetime(str(latest.get("created_at") or ""))
    if not first_at or not latest_at:
        return 0
    return int(max(0, (latest_at - first_at).total_seconds()))


def expected_launchd_runs_in_window(observed_window_seconds: int, run_interval_seconds: Any) -> int:
    if not isinstance(run_interval_seconds, int) or run_interval_seconds <= 0:
        return 0
    if observed_window_seconds < run_interval_seconds:
        return 0
    return int(observed_window_seconds // run_interval_seconds)


def launchd_audit_recommendations(alerts: list[dict[str, Any]]) -> list[str]:
    if not alerts:
        return ["No action required."]
    codes = {str(alert.get("code") or "") for alert in alerts}
    recommendations: list[str] = []
    if "monitor_history_missing" in codes or "insufficient_monitor_history" in codes:
        recommendations.append("Collect more monitor snapshots before claiming long-term stability.")
    if "launchd_not_loaded" in codes:
        recommendations.append("Check launchctl bootstrap state for the semantic maintenance LaunchAgent.")
    if "last_exit_nonzero" in codes or "maintain_status_failed" in codes or "prune_status_failed" in codes:
        recommendations.append("Open the latest semantic maintain/prune reports and stderr log before trusting background refresh.")
    if "natural_run_overdue" in codes or "missing_expected_runs" in codes or "latest_snapshot_stale" in codes:
        recommendations.append("Run semantic-launchd-status --with-launchctl and inspect whether the LaunchAgent is still scheduled.")
    if "stderr_has_output" in codes:
        recommendations.append("Review the LaunchAgent stderr log; audit stores only the size, not the log text.")
    return recommendations or ["Review audit alerts."]


def render_launchd_audit_markdown(result: dict[str, Any]) -> str:
    alerts = result.get("alerts") if isinstance(result.get("alerts"), list) else []
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    alert_lines = [
        f"- `{alert.get('severity', '')}` `{alert.get('code', '')}`: {alert.get('message', '')}"
        for alert in alerts
    ] or ["- none"]
    recommendation_lines = [f"- {item}" for item in result.get("recommendations") or []] or ["- none"]
    return "\n".join(
        [
            "# Semantic LaunchAgent Audit",
            "",
            f"- Health: `{result.get('health', '')}`",
            f"- Status: `{result.get('status', '')}`",
            f"- Run id: `{result.get('run_id', '')}`",
            f"- History path: `{result.get('history_path', '')}`",
            f"- Latest monitor health: `{summary.get('latest_health', '')}`",
            f"- Latest runs: `{summary.get('latest_runs')}`",
            f"- Runs delta: `{summary.get('runs_delta', 0)}`",
            f"- Latest activity: `{summary.get('latest_launchd_activity_at', '')}`",
            f"- Next expected run after: `{summary.get('next_expected_run_after', '')}`",
            f"- Natural run overdue: `{summary.get('natural_run_overdue', False)}`",
            f"- Consecutive unhealthy snapshots: `{metrics.get('consecutive_unhealthy_snapshots', 0)}`",
            "",
            "## Alerts",
            "",
            *alert_lines,
            "",
            "## Recommendations",
            "",
            *recommendation_lines,
            "",
        ]
    )


def latest_semantic_launchd_audit(out_root: Path) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    latest_json_path = out_root / "reports" / "semantic-launchd-audit-latest.json"
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "summary": {},
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "error": str(exc),
            "summary": {},
        }
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "health": data.get("health", ""),
        "summary": {
            "status": data.get("status", ""),
            "health": data.get("health", ""),
            "alerts": len(data.get("alerts") or []),
            "recommendations": len(data.get("recommendations") or []),
            "run_id": data.get("run_id", ""),
            "started_at": data.get("started_at", ""),
        },
    }


def launchd_run_advanced(initial_runs: Any, initial_activity: str, latest: dict[str, Any]) -> tuple[bool, str]:
    summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
    latest_runs = summary.get("latest_runs")
    if isinstance(initial_runs, int) and isinstance(latest_runs, int) and latest_runs > initial_runs:
        return True, "runs_increased"
    latest_activity = str(summary.get("latest_launchd_activity_at") or "")
    initial_dt = parse_iso_datetime(initial_activity) if initial_activity else None
    latest_dt = parse_iso_datetime(latest_activity) if latest_activity else None
    if initial_dt and latest_dt and latest_dt > initial_dt:
        return True, "launchd_activity_advanced"
    if not initial_dt and latest_dt:
        return True, "launchd_activity_observed"
    return False, ""


def compact_wait_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "created_at": summary.get("latest_created_at", ""),
        "health": summary.get("latest_health", ""),
        "runs": summary.get("latest_runs"),
        "state": summary.get("latest_state", ""),
        "last_exit_code": summary.get("latest_last_exit_code", ""),
        "latest_launchd_activity_at": summary.get("latest_launchd_activity_at", ""),
        "next_expected_run_after": summary.get("next_expected_run_after", ""),
        "natural_run_due": summary.get("natural_run_due", False),
        "natural_run_overdue": summary.get("natural_run_overdue", False),
    }


def render_launchd_wait_markdown(result: dict[str, Any]) -> str:
    latest = result.get("latest_summary") if isinstance(result.get("latest_summary"), dict) else {}
    return "\n".join(
        [
            "# Semantic LaunchAgent Wait",
            "",
            f"- Status: `{result.get('status', '')}`",
            f"- Stop reason: `{result.get('stop_reason', '')}`",
            f"- Initial runs: `{result.get('initial_runs')}`",
            f"- Latest runs: `{latest.get('latest_runs')}`",
            f"- Initial activity: `{result.get('initial_launchd_activity_at', '')}`",
            f"- Latest activity: `{latest.get('latest_launchd_activity_at', '')}`",
            f"- Next expected run after: `{latest.get('next_expected_run_after', '')}`",
            f"- Natural run overdue: `{latest.get('natural_run_overdue', False)}`",
            f"- Snapshots: `{len(result.get('snapshots') or [])}`",
            "",
        ]
    )


def latest_semantic_launchd_monitor(out_root: Path) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    latest_json_path = out_root / "reports" / "semantic-launchd-monitor-latest.json"
    if not latest_json_path.exists():
        return {
            "exists": False,
            "path": str(latest_json_path),
            "summary": {},
        }
    try:
        data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "path": str(latest_json_path),
            "error": str(exc),
            "summary": {},
        }
    return {
        "exists": True,
        "path": str(latest_json_path),
        "latest_md_path": data.get("latest_md_path", ""),
        "history_path": data.get("history_path", ""),
        "summary": data.get("summary") if isinstance(data.get("summary"), dict) else {},
    }


def semantic_launchd_monitor_snapshot(status: dict[str, Any]) -> dict[str, Any]:
    launchctl = status.get("launchctl") if isinstance(status.get("launchctl"), dict) else {}
    reports = status.get("reports") if isinstance(status.get("reports"), dict) else {}
    logs = status.get("logs") if isinstance(status.get("logs"), dict) else {}
    return {
        "semantic_launchd_monitor_version": LAUNCHD_MONITOR_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "label": status.get("label", ""),
        "out_root": status.get("out_root", ""),
        "health": status.get("health", "unknown"),
        "installed": bool(status.get("installed", False)),
        "issues": list(status.get("issues") or []),
        "launchctl": {
            "checked": bool(launchctl.get("checked", False)),
            "loaded": bool(launchctl.get("loaded", False)),
            "state": launchctl.get("state", ""),
            "runs": launchctl.get("runs"),
            "last_exit_code": launchctl.get("last_exit_code", ""),
            "run_interval_seconds": launchctl.get("run_interval_seconds"),
            "target": launchctl.get("target", ""),
        },
        "reports": {
            "semantic_maintain": compact_launchd_report(reports.get("semantic_maintain")),
            "semantic_ann_prune": compact_launchd_report(reports.get("semantic_ann_prune")),
        },
        "logs": {
            "stdout_size_bytes": int((logs.get("stdout") or {}).get("size_bytes") or 0),
            "stderr_size_bytes": int((logs.get("stderr") or {}).get("size_bytes") or 0),
            "stderr_tail": list((logs.get("stderr") or {}).get("tail") or [])[-5:],
        },
    }


def compact_launchd_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"exists": False, "path": "", "summary": {}}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    keys = [
        "run_id",
        "status",
        "stop_reason",
        "source",
        "backend",
        "started_at",
        "processed",
        "jobs_run",
        "skipped",
        "dry_run",
        "files_removed",
        "bytes_removed",
    ]
    return {
        "exists": bool(report.get("exists", False)),
        "path": report.get("path", ""),
        "summary": {key: summary.get(key) for key in keys if key in summary},
    }


def summarize_launchd_monitor_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {
            "snapshots": 0,
            "latest_health": "unknown",
            "unhealthy_snapshots": 0,
            "runs_delta": 0,
        }
    first_launchctl = history[0].get("launchctl") or {}
    latest = history[-1]
    latest_launchctl = latest.get("launchctl") or {}
    first_runs = first_launchctl.get("runs")
    latest_runs = latest_launchctl.get("runs")
    runs_delta = (latest_runs - first_runs) if isinstance(first_runs, int) and isinstance(latest_runs, int) else 0
    unhealthy = [item for item in history if item.get("health") != "ok"]
    timing = launchd_monitor_timing(latest)
    return {
        "snapshots": len(history),
        "latest_created_at": latest.get("created_at", ""),
        "latest_health": latest.get("health", "unknown"),
        "latest_issues": latest.get("issues") or [],
        "unhealthy_snapshots": len(unhealthy),
        "latest_loaded": bool(latest_launchctl.get("loaded", False)),
        "latest_state": latest_launchctl.get("state", ""),
        "latest_runs": latest_runs,
        "latest_last_exit_code": latest_launchctl.get("last_exit_code", ""),
        "runs_delta": runs_delta,
        "latest_maintain_status": ((latest.get("reports") or {}).get("semantic_maintain") or {}).get("summary", {}).get("status", ""),
        "latest_prune_status": ((latest.get("reports") or {}).get("semantic_ann_prune") or {}).get("summary", {}).get("status", ""),
        **timing,
    }


def launchd_monitor_timing(latest: dict[str, Any]) -> dict[str, Any]:
    latest_launchctl = latest.get("launchctl") or {}
    created_at = parse_iso_datetime(str(latest.get("created_at") or ""))
    latest_activity_at = latest_launchd_activity_at(latest) or created_at
    interval_seconds = latest_launchctl.get("run_interval_seconds")
    now = datetime.now().astimezone()
    if created_at and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=now.tzinfo)
    if latest_activity_at and latest_activity_at.tzinfo is None:
        latest_activity_at = latest_activity_at.replace(tzinfo=now.tzinfo)
    age_seconds = int(max(0, (now - created_at).total_seconds())) if created_at else None
    if latest_activity_at and isinstance(interval_seconds, int) and interval_seconds > 0:
        next_due = latest_activity_at + timedelta(seconds=interval_seconds)
        seconds_until_due = int((next_due - now).total_seconds())
        seconds_overdue = max(0, -seconds_until_due)
        return {
            "latest_snapshot_age_seconds": age_seconds,
            "latest_launchd_activity_at": latest_activity_at.isoformat(),
            "next_expected_run_after": next_due.isoformat(),
            "seconds_until_next_expected_run": seconds_until_due,
            "natural_run_due": seconds_until_due <= 0,
            "seconds_overdue": seconds_overdue,
            "natural_run_overdue": seconds_overdue > DEFAULT_LAUNCHD_OVERDUE_GRACE_SECONDS,
            "overdue_grace_seconds": DEFAULT_LAUNCHD_OVERDUE_GRACE_SECONDS,
        }
    return {
        "latest_snapshot_age_seconds": age_seconds,
        "latest_launchd_activity_at": latest_activity_at.isoformat() if latest_activity_at else "",
        "next_expected_run_after": "",
        "seconds_until_next_expected_run": None,
        "natural_run_due": False,
        "seconds_overdue": 0,
        "natural_run_overdue": False,
        "overdue_grace_seconds": DEFAULT_LAUNCHD_OVERDUE_GRACE_SECONDS,
    }


def latest_launchd_activity_at(latest: dict[str, Any]) -> datetime | None:
    reports = latest.get("reports") if isinstance(latest.get("reports"), dict) else {}
    candidates: list[datetime] = []
    for key in ("semantic_maintain", "semantic_ann_prune"):
        report = reports.get(key) if isinstance(reports.get(key), dict) else {}
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        started_at = parse_iso_datetime(str(summary.get("started_at") or ""))
        if started_at:
            candidates.append(started_at)
    return max(candidates) if candidates else None


def parse_iso_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def render_launchd_monitor_markdown(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    snapshot = result.get("snapshot") or {}
    launchctl = snapshot.get("launchctl") or {}
    reports = snapshot.get("reports") or {}
    maintain = (reports.get("semantic_maintain") or {}).get("summary", {})
    prune = (reports.get("semantic_ann_prune") or {}).get("summary", {})
    return "\n".join(
        [
            "# Semantic LaunchAgent Monitor",
            "",
            f"- Health: `{snapshot.get('health', 'unknown')}`",
            f"- Installed: `{snapshot.get('installed', False)}`",
            f"- Loaded: `{launchctl.get('loaded', False)}`",
            f"- State: `{launchctl.get('state', '')}`",
            f"- Runs: `{launchctl.get('runs')}`",
            f"- Last exit code: `{launchctl.get('last_exit_code', '')}`",
            f"- Run interval seconds: `{launchctl.get('run_interval_seconds')}`",
            f"- Issues: `{', '.join(snapshot.get('issues') or []) or 'none'}`",
            "",
            "## Latest Reports",
            "",
            f"- Maintain: `{maintain.get('status', '')}` `{maintain.get('run_id', '')}` processed `{maintain.get('processed', 0)}`",
            f"- Prune: `{prune.get('status', '')}` `{prune.get('run_id', '')}` removed `{prune.get('files_removed', 0)}` files",
            "",
            "## History Window",
            "",
            f"- Snapshots: `{summary.get('snapshots', 0)}`",
            f"- Unhealthy snapshots: `{summary.get('unhealthy_snapshots', 0)}`",
            f"- Runs delta: `{summary.get('runs_delta', 0)}`",
            f"- Latest snapshot age seconds: `{summary.get('latest_snapshot_age_seconds')}`",
            f"- Latest launchd activity at: `{summary.get('latest_launchd_activity_at', '')}`",
            f"- Next expected run after: `{summary.get('next_expected_run_after', '')}`",
            f"- Natural run due: `{summary.get('natural_run_due', False)}`",
            f"- Natural run overdue: `{summary.get('natural_run_overdue', False)}`",
            f"- Seconds overdue: `{summary.get('seconds_overdue', 0)}`",
            "",
        ]
    )


def launchd_paths(out_root: Path, label: str, launch_agents_dir: Path | None = None) -> dict[str, Path]:
    scripts_dir = out_root / "scripts"
    logs_dir = out_root / "logs"
    plist_dir = launch_agents_dir or (Path.home() / "Library" / "LaunchAgents")
    return {
        "script_path": scripts_dir / f"{label}.sh",
        "stdout_path": logs_dir / f"{label}.out.log",
        "stderr_path": logs_dir / f"{label}.err.log",
        "plist_path": plist_dir.expanduser().resolve() / f"{label}.plist",
    }


def inspect_launchd_plist(
    plist_path: Path,
    expected_label: str,
    paths: dict[str, Path],
    issues: list[str],
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "exists": plist_path.exists(),
        "valid": False,
        "label_matches": False,
        "program_matches": False,
        "stdout_matches": False,
        "stderr_matches": False,
        "start_interval_seconds": None,
        "read_error": "",
    }
    if not status["exists"]:
        return status
    try:
        plist = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        status["read_error"] = str(exc)
        issues.append("plist_unreadable")
        return status

    program_arguments = plist.get("ProgramArguments") if isinstance(plist, dict) else None
    status["valid"] = isinstance(plist, dict)
    status["label_matches"] = plist.get("Label") == expected_label if isinstance(plist, dict) else False
    status["program_matches"] = program_arguments == [str(paths["script_path"])]
    status["stdout_matches"] = plist.get("StandardOutPath") == str(paths["stdout_path"]) if isinstance(plist, dict) else False
    status["stderr_matches"] = plist.get("StandardErrorPath") == str(paths["stderr_path"]) if isinstance(plist, dict) else False
    status["start_interval_seconds"] = plist.get("StartInterval") if isinstance(plist, dict) else None
    for key, issue in [
        ("valid", "plist_invalid"),
        ("label_matches", "plist_label_mismatch"),
        ("program_matches", "plist_program_mismatch"),
        ("stdout_matches", "plist_stdout_mismatch"),
        ("stderr_matches", "plist_stderr_mismatch"),
    ]:
        if not status[key]:
            issues.append(issue)
    return status


def inspect_launchd_script(script_path: Path, issues: list[str]) -> dict[str, Any]:
    status: dict[str, Any] = {
        "exists": script_path.exists(),
        "executable": False,
        "has_semantic_maintain": False,
        "has_semantic_ann_prune": False,
        "read_error": "",
    }
    if not status["exists"]:
        return status
    try:
        script_text = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        status["read_error"] = str(exc)
        issues.append("script_unreadable")
        return status
    status["executable"] = bool(script_path.stat().st_mode & 0o111)
    status["has_semantic_maintain"] = "semantic-maintain" in script_text
    status["has_semantic_ann_prune"] = "semantic-ann-prune" in script_text
    for key, issue in [
        ("executable", "script_not_executable"),
        ("has_semantic_maintain", "script_missing_semantic_maintain"),
        ("has_semantic_ann_prune", "script_missing_semantic_ann_prune"),
    ]:
        if not status[key]:
            issues.append(issue)
    return status


def inspect_launchd_log(path: Path, tail_lines: int) -> dict[str, Any]:
    status: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "tail": [],
        "read_error": "",
    }
    if not status["exists"]:
        return status
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        status["read_error"] = str(exc)
        return status
    status["tail"] = lines[-max(0, int(tail_lines)) :]
    return status


def inspect_launchctl(label: str) -> dict[str, Any]:
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    try:
        result = subprocess.run(
            ["launchctl", "print", target],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "checked": True,
            "domain": domain,
            "target": target,
            "loaded": False,
            "returncode": None,
            "error": str(exc),
            "summary": [],
        }
    output = (result.stdout or result.stderr or "").splitlines()
    details = parse_launchctl_print(output)
    return {
        "checked": True,
        "domain": domain,
        "target": target,
        "loaded": result.returncode == 0,
        "returncode": result.returncode,
        "error": "" if result.returncode == 0 else "\n".join(output[:3]),
        "summary": output[:20],
        **details,
    }


def parse_launchctl_print(lines: list[str]) -> dict[str, Any]:
    details: dict[str, Any] = {
        "state": "",
        "runs": None,
        "last_exit_code": "",
        "run_interval_seconds": None,
        "program": "",
        "path": "",
        "stdout_path": "",
        "stderr_path": "",
    }
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("state = ") and not details["state"]:
            details["state"] = line.removeprefix("state = ").strip()
        elif line.startswith("runs = ") and details["runs"] is None:
            details["runs"] = parse_int_or_none(line.removeprefix("runs = ").strip())
        elif line.startswith("last exit code = ") and not details["last_exit_code"]:
            details["last_exit_code"] = line.removeprefix("last exit code = ").strip()
        elif line.startswith("run interval = ") and line.endswith(" seconds") and details["run_interval_seconds"] is None:
            value = line.removeprefix("run interval = ").removesuffix(" seconds").strip()
            details["run_interval_seconds"] = parse_int_or_none(value)
        elif line.startswith("program = ") and not details["program"]:
            details["program"] = line.removeprefix("program = ").strip()
        elif line.startswith("path = ") and not details["path"]:
            details["path"] = line.removeprefix("path = ").strip()
        elif line.startswith("stdout path = ") and not details["stdout_path"]:
            details["stdout_path"] = line.removeprefix("stdout path = ").strip()
        elif line.startswith("stderr path = ") and not details["stderr_path"]:
            details["stderr_path"] = line.removeprefix("stderr path = ").strip()
    return details


def parse_int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def latest_launchd_report(out_root: Path, pattern: str) -> dict[str, Any]:
    reports_dir = out_root / "reports"
    candidates = sorted(
        (path for path in reports_dir.glob(pattern) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return {"exists": False, "path": "", "summary": {}, "read_error": ""}
    path = candidates[0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "path": str(path), "summary": {}, "read_error": str(exc)}
    return {
        "exists": True,
        "path": str(path),
        "summary": summarize_launchd_report(data),
        "read_error": "",
    }


def summarize_launchd_report(data: dict[str, Any]) -> dict[str, Any]:
    summary = {
        key: data.get(key)
        for key in [
            "run_id",
            "status",
            "stop_reason",
            "source",
            "backend",
            "started_at",
            "dry_run",
            "processed",
            "skipped",
            "jobs_run",
            "files_seen",
            "files_removed",
            "bytes_removed",
            "caches_seen",
            "kept_entries",
        ]
        if key in data
    }
    return summary


def normalize_label(label: str) -> str:
    cleaned = "".join(char for char in label.strip() if char.isalnum() or char in ".-_")
    return cleaned or DEFAULT_LAUNCHD_LABEL


def render_semantic_maintenance_script(out_root: Path, config: dict[str, Any], paths: dict[str, Path]) -> str:
    quoted_bin = shlex.quote(config["agent_context_bin"])
    quoted_out = shlex.quote(str(out_root))
    quoted_log_dir = shlex.quote(str(paths["stdout_path"].parent))
    quoted_source = shlex.quote(config["source"])
    quoted_label = shlex.quote(config["label"])
    return "\n".join(
        [
            "#!/bin/sh",
            "set -eu",
            f"mkdir -p {quoted_log_dir}",
            f"export AGENT_CONTEXT_ROOT={quoted_out}",
            f"{quoted_bin} semantic-maintain --out {quoted_out} --source {quoted_source} "
            f"--budget {config['budget']} --max-jobs {config['max_jobs']} "
            f"--min-interval-minutes {config['min_interval_minutes']}",
            f"{quoted_bin} semantic-ann-prune --out {quoted_out} "
            f"--max-entries {config['ann_max_entries']} --max-bytes {config['ann_max_bytes']}",
            f"{quoted_bin} semantic-launchd-monitor --out {quoted_out} --label {quoted_label} "
            "--tail-lines 40 --with-launchctl",
            "",
        ]
    )


def render_launchd_plist(label: str, paths: dict[str, Path], interval_minutes: int) -> dict[str, Any]:
    return {
        "Label": label,
        "ProgramArguments": [str(paths["script_path"])],
        "StartInterval": max(1, int(interval_minutes)) * 60,
        "RunAtLoad": False,
        "StandardOutPath": str(paths["stdout_path"]),
        "StandardErrorPath": str(paths["stderr_path"]),
        "EnvironmentVariables": {
            "PATH": DEFAULT_LAUNCHD_PATH,
        },
    }
