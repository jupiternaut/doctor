from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text


CODEX_PLUS_SMOKE_VERSION = "0.1"
CODEX_PLUS_ROOT_ENV_VARS = ("DOCTOR_CODEX_PLUS_ROOT", "CODEX_PLUS_ROOT")


def run_codex_plus_smoke(
    out_root: Path,
    *,
    codex_plus_root: Path | None = None,
    timeout_seconds: int = 120,
    run_panel_status: bool = True,
    run_manager_feedback: bool = False,
    run_runtime: bool = False,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    root = normalized_codex_plus_root(codex_plus_root)
    started_at = datetime.now().astimezone()
    report_id = started_at.strftime("%Y%m%d%H%M%S%f")
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"codex-plus-smoke-{report_id}.json"
    md_path = reports_dir / f"codex-plus-smoke-{report_id}.md"
    latest_json_path = reports_dir / "codex-plus-smoke-latest.json"
    latest_md_path = reports_dir / "codex-plus-smoke-latest.md"
    node = shutil.which("node")
    scripts = smoke_scripts(
        root,
        run_panel_status=run_panel_status,
        run_manager_feedback=run_manager_feedback,
        run_runtime=run_runtime,
    )
    if not root:
        script_results = []
        status = "failed"
        error = "Codex++ repo was not found."
    elif not node:
        script_results = []
        status = "failed"
        error = "node executable was not found."
    elif not scripts:
        script_results = []
        status = "warning"
        error = "No Codex++ smoke scripts were selected."
    else:
        script_results = [
            run_node_script(
                node,
                script,
                cwd=root,
                out_root=out_root,
                timeout_seconds=max(5, timeout_seconds),
            )
            for script in scripts
        ]
        status = "ok" if all(item["status"] == "ok" for item in script_results) else "failed"
        error = "; ".join(item.get("error", "") for item in script_results if item.get("error"))
    report = {
        "codex_plus_smoke_version": CODEX_PLUS_SMOKE_VERSION,
        "created_at": started_at.isoformat(),
        "out_root": str(out_root),
        "codex_plus_root": str(root or ""),
        "status": status,
        "error": error,
        "scripts": script_results,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, payload)
    write_text(latest_json_path, payload)
    markdown = render_codex_plus_smoke_markdown(report)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return report


def latest_codex_plus_smoke_status(out_root: Path) -> dict[str, Any]:
    path = out_root.expanduser().resolve() / "reports" / "codex-plus-smoke-latest.json"
    if not path.exists():
        return {"exists": False, "path": str(path), "status": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "path": str(path), "status": "failed", "error": str(exc)}
    return {
        "exists": True,
        "path": str(path),
        "latest_md_path": payload.get("latest_md_path") or "",
        "status": payload.get("status") or "unknown",
        "created_at": payload.get("created_at") or "",
        "scripts": [
            {
                "name": item.get("name", ""),
                "status": item.get("status", ""),
                "returncode": item.get("returncode"),
                "summary": item.get("summary") or {},
            }
            for item in payload.get("scripts") or []
            if isinstance(item, dict)
        ],
    }


def smoke_scripts(
    root: Path | None,
    *,
    run_panel_status: bool,
    run_manager_feedback: bool,
    run_runtime: bool,
) -> list[dict[str, Any]]:
    if not root:
        return []
    scripts = []
    if run_panel_status:
        scripts.append({"name": "panel_status", "path": root / "scripts" / "smoke-agent-context-panel-status.mjs"})
    if run_manager_feedback:
        scripts.append(
            {
                "name": "manager_feedback_replay",
                "path": root / "scripts" / "smoke-agent-context-manager-feedback-replay.mjs",
            }
        )
    if run_runtime:
        scripts.append({"name": "runtime", "path": root / "scripts" / "smoke-agent-context-runtime.mjs"})
    return scripts


def run_node_script(
    node: str,
    script: dict[str, Any],
    *,
    cwd: Path,
    out_root: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    path = Path(script["path"])
    if not path.exists():
        return {
            "name": script["name"],
            "path": str(path),
            "status": "failed",
            "returncode": None,
            "error": "script not found",
            "stdout": "",
            "stderr": "",
            "summary": {},
        }
    env = {
        **os.environ,
        "AGENT_CONTEXT_ROOT": str(out_root),
        "AGENT_CONTEXT_BIN": str(out_root / "agent-context"),
    }
    try:
        completed = subprocess.run(
            [node, str(path)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": script["name"],
            "path": str(path),
            "status": "failed",
            "returncode": None,
            "error": f"timed out after {timeout_seconds}s",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "summary": {},
        }
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    parsed = parse_last_json(stdout)
    return {
        "name": script["name"],
        "path": str(path),
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "error": "" if completed.returncode == 0 else stderr.strip() or stdout.strip()[-1000:],
        "stdout": trim_output(stdout),
        "stderr": trim_output(stderr),
        "summary": parsed if isinstance(parsed, dict) else {},
    }


def parse_last_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {}
    for index, char in enumerate(text):
        if char != "{":
            continue
        candidate = text[index:]
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def trim_output(text: str, *, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def normalized_codex_plus_root(codex_plus_root: Path | None) -> Path | None:
    if codex_plus_root:
        root = codex_plus_root.expanduser().resolve()
        return root if root.exists() else None
    for name in CODEX_PLUS_ROOT_ENV_VARS:
        value = os.environ.get(name)
        if not value:
            continue
        root = Path(value).expanduser().resolve()
        if root.exists():
            return root
    return None


def render_codex_plus_smoke_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Codex++ Agent Context Smoke",
        "",
        f"- status: `{report.get('status')}`",
        f"- created_at: `{report.get('created_at')}`",
        f"- codex_plus_root: `{report.get('codex_plus_root')}`",
        f"- error: `{report.get('error', '')}`",
        "",
        "## Scripts",
        "",
        "| Name | Status | Return Code | Path |",
        "| --- | --- | ---: | --- |",
    ]
    for item in report.get("scripts") or []:
        lines.append(
            f"| {item.get('name', '')} | `{item.get('status', '')}` | {item.get('returncode')} | `{item.get('path', '')}` |"
        )
    key_status_lines = codex_plus_smoke_key_status_lines(report)
    if key_status_lines:
        lines.extend(["", "## Key Status", "", *key_status_lines])
    lines.append("")
    return "\n".join(lines)


def codex_plus_smoke_key_status_lines(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in report.get("scripts") or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        stage = summary.get("v1StageStatus") if isinstance(summary.get("v1StageStatus"), dict) else {}
        if stage:
            lines.append(
                "- "
                f"`{item.get('name', '')}` v1 stage: `{stage.get('status', '')}`, "
                f"ok={stage.get('ok', 0)}, waiting={stage.get('waitingForTime', 0)}, "
                f"report=`{stage.get('reportMarkdownPath', '')}`"
            )
            continue
        if summary.get("v1StageStatus"):
            lines.append(
                "- "
                f"`{item.get('name', '')}` v1 stage: `{summary.get('v1StageStatus', '')}`, "
                f"ok={summary.get('v1StageOk', 0)}, waiting={summary.get('v1StageWaiting', 0)}, "
                f"report=`{summary.get('v1StageReport', '')}`"
            )
    return lines
