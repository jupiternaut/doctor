from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_preflight import run_agent_preflight
from .io import write_text
from .runtime_review_client import export_runtime_review_launch
from .runtime_vm import inspect_runtime_session


RUNTIME_TASK_VERSION = "0.1"


def start_runtime_task(
    out_root: str | Path,
    goal: str,
    *,
    session_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    image_paths: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    preflight = run_agent_preflight(
        root,
        advance="clarify",
        goal=goal,
        session_id=session_id,
        image_paths=image_paths,
    )
    resolved_session_id = str(preflight["session_id"])
    launch = export_runtime_review_launch(root, resolved_session_id, host=host, port=max(1, int(port)))
    session = inspect_runtime_session(root, resolved_session_id)
    result = build_runtime_task_result(
        root,
        goal,
        session,
        preflight=preflight,
        launch=launch,
        host=host,
        port=max(1, int(port)),
    )
    persist_runtime_task(result)
    return result


def build_runtime_task_result(
    root: Path,
    goal: str,
    session: dict[str, Any],
    *,
    preflight: dict[str, Any],
    launch: dict[str, Any],
    host: str,
    port: int,
) -> dict[str, Any]:
    session_id = str(session["session_id"])
    session_dir = root / "runtime" / "sessions" / session_id
    json_path = session_dir / "runtime_task.json"
    md_path = session_dir / "runtime_task.md"
    next_state = session.get("next") or {}
    files = session.get("files") or {}
    return {
        "runtime_task_version": RUNTIME_TASK_VERSION,
        "status": session.get("status"),
        "created_at": datetime.now().astimezone().isoformat(),
        "goal": goal,
        "session_id": session_id,
        "out_root": str(root),
        "stage": "clarify_review",
        "doctor_access": False,
        "resolver_allowed": False,
        "index_access_allowed": False,
        "host": host,
        "port": int(port),
        "review_file": next_state.get("review_file"),
        "next_message": next_state.get("message"),
        "next_commands": next_state.get("commands") or [],
        "agent_preflight": summarize_preflight(preflight),
        "review_launch": summarize_launch(launch),
        "client_html_path": launch.get("client_html_path"),
        "review_server_url": launch.get("review_server_url"),
        "start_server_command": launch.get("start_server_command"),
        "open_client_command": launch.get("open_client_command"),
        "runtime_task_json_path": str(json_path),
        "runtime_task_md_path": str(md_path),
        "files": files,
        "runtime_session": session,
    }


def summarize_preflight(preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": preflight.get("status"),
        "advance": preflight.get("advance"),
        "doctor_access": preflight.get("doctor_access"),
        "resolver_allowed": preflight.get("resolver_allowed"),
        "review_file": preflight.get("review_file"),
        "agent_preflight_json_path": preflight.get("agent_preflight_json_path"),
        "agent_preflight_md_path": preflight.get("agent_preflight_md_path"),
        "client_contract": preflight.get("client_contract") or {},
    }


def summarize_launch(launch: dict[str, Any]) -> dict[str, Any]:
    files = launch.get("files") or {}
    return {
        "status": launch.get("status"),
        "review_server_url": launch.get("review_server_url"),
        "api_session_url": launch.get("api_session_url"),
        "api_action_url": launch.get("api_action_url"),
        "client_html_path": launch.get("client_html_path"),
        "review_launch_json_path": files.get("launch_json"),
        "review_launch_md_path": files.get("launch_md"),
        "start_server_command": launch.get("start_server_command"),
        "open_client_command": launch.get("open_client_command"),
    }


def persist_runtime_task(result: dict[str, Any]) -> None:
    json_path = Path(str(result["runtime_task_json_path"]))
    md_path = Path(str(result["runtime_task_md_path"]))
    write_text(json_path, json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(md_path, render_runtime_task_markdown(result))


def render_runtime_task_markdown(result: dict[str, Any]) -> str:
    preflight = result.get("agent_preflight") or {}
    launch = result.get("review_launch") or {}
    next_command = next(iter(result.get("next_commands") or []), "")
    lines = [
        "---",
        f"runtime_task_version: {result['runtime_task_version']}",
        f"status: {result['status']}",
        f"session_id: {result['session_id']}",
        "stage: clarify_review",
        "doctor_access: false",
        "resolver_allowed: false",
        "---",
        "",
        "# Doctor Runtime Task",
        "",
        "This is the one-shot task entrypoint for Codex++, Warp, Codex CLI, or MCP clients. It starts the first review gate only: clarify the user goal before Doctor reads local indexes or provider manifests.",
        "",
        "## User Goal",
        "",
        str(result.get("goal") or ""),
        "",
        "## Review Gate",
        "",
        "- Stage: `clarify_review`",
        "- Doctor index access: `false`",
        "- Safe model payload: `false`",
        f"- Review file: `{result.get('review_file')}`",
        f"- Agent preflight: `{preflight.get('agent_preflight_md_path')}`",
        f"- Launch contract: `{launch.get('review_launch_md_path')}`",
        f"- Review client: `{result.get('client_html_path')}`",
        "",
        "## Start Review UI",
        "",
        "Start the local review server:",
        "",
        "```bash",
        str(result.get("start_server_command") or ""),
        "```",
        "",
        "Open the generated review client:",
        "",
        "```bash",
        str(result.get("open_client_command") or ""),
        "```",
        "",
        "## Next Step",
        "",
        "After the user accepts `refined_prompt.md`, generate a reviewable `model_input.md` with:",
        "",
        "```bash",
        str(next_command),
        "```",
        "",
    ]
    return "\n".join(lines)
