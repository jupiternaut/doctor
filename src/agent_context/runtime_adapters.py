from __future__ import annotations

import json
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text
from .runtime_vm import inspect_runtime_session


RUNTIME_ADAPTER_VERSION = "0.1"
DEFAULT_TARGETS = ["codex-plus", "warp", "codex-cli", "mcp"]


def export_runtime_adapter_package(
    out_root: str | Path,
    session_id: str,
    *,
    targets: list[str] | None = None,
    agent_command: str = "<agent command>",
    review_port: int = 8765,
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    session = inspect_runtime_session(root, session_id)
    adapter_dir = ensure_dir(root / "runtime" / "sessions" / session_id / "adapters")
    selected_targets = normalize_targets(targets)
    now = datetime.now().astimezone().isoformat()
    files = session["files"]
    manifest_path = adapter_dir / "adapter_manifest.json"
    overview_path = adapter_dir / "DOCTOR_RUNTIME_ADAPTER.md"
    env_path = adapter_dir / "doctor-runtime-env.sh"
    codex_cli_path = adapter_dir / "codex-cli-runtime.sh"
    mcp_sequence_path = adapter_dir / "mcp_tool_sequence.json"
    target_docs = {
        "codex-plus": adapter_dir / "codex-plus-runtime.md",
        "warp": adapter_dir / "warp-runtime.md",
        "codex-cli": adapter_dir / "codex-cli-runtime.md",
        "mcp": adapter_dir / "mcp-runtime.md",
    }
    adapter_files = {
        "manifest": str(manifest_path),
        "overview": str(overview_path),
        "env": str(env_path),
        "codex_cli_wrapper": str(codex_cli_path),
        "mcp_tool_sequence": str(mcp_sequence_path),
    }
    adapter_files.update({f"{target}_doc": str(path) for target, path in target_docs.items() if target in selected_targets})
    manifest = {
        "runtime_adapter_version": RUNTIME_ADAPTER_VERSION,
        "status": "ready",
        "created_at": now,
        "session_id": session_id,
        "out_root": str(root),
        "targets": selected_targets,
        "agent_command": agent_command,
        "review_server_url": f"http://127.0.0.1:{int(review_port)}/",
        "runtime_status": session["status"],
        "current_review_file": session["next"].get("review_file"),
        "next_commands": session["next"].get("commands") or [],
        "files": files,
        "adapter_files": adapter_files,
        "entrypoints": adapter_entrypoints(root, session_id, agent_command=agent_command, review_port=review_port),
        "mcp_tool_sequence": mcp_tool_sequence(session_id, agent_command=agent_command),
    }
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(overview_path, render_adapter_overview(manifest))
    write_text(env_path, render_env_script(root, session_id, manifest))
    write_text(codex_cli_path, render_codex_cli_wrapper(root, session_id, manifest))
    make_executable(env_path)
    make_executable(codex_cli_path)
    write_text(mcp_sequence_path, json.dumps(manifest["mcp_tool_sequence"], ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    for target in selected_targets:
        write_text(target_docs[target], render_target_doc(manifest, target))
    manifest["runtime_session"] = inspect_runtime_session(root, session_id)
    return manifest


def normalize_targets(targets: list[str] | None) -> list[str]:
    if not targets:
        return list(DEFAULT_TARGETS)
    normalized: list[str] = []
    for target in targets:
        value = target.strip()
        if not value:
            continue
        if value == "all":
            return list(DEFAULT_TARGETS)
        if value not in DEFAULT_TARGETS:
            raise ValueError(f"unknown runtime adapter target: {value}")
        if value not in normalized:
            normalized.append(value)
    return normalized or list(DEFAULT_TARGETS)


def adapter_entrypoints(root: Path, session_id: str, *, agent_command: str, review_port: int) -> dict[str, str]:
    return {
        "inspect": doctor_command(root, "session", "--session-id", session_id),
        "agent_preflight_clarify": doctor_command(root, "agent-preflight", "--session-id", session_id, "--advance", "clarify", "--goal", quote_arg("<user task>")),
        "agent_preflight_context": doctor_command(root, "agent-preflight", "--session-id", session_id, "--advance", "context", "--source-scope", "all", "--limit", "8"),
        "agent_preflight_handoff": doctor_command(root, "agent-preflight", "--session-id", session_id, "--advance", "handoff", "--agent-command", quote_arg(agent_command)),
        "review_server": doctor_command(root, "runtime-review-server", "--session-id", session_id, "--port", str(int(review_port))),
        "generate_context": doctor_command(root, "context-review", "--session-id", session_id, "--action", "generate", "--source-scope", "all", "--limit", "8"),
        "export_handoff": doctor_command(root, "runtime-handoff", "--session-id", session_id),
        "export_adapter": doctor_command(root, "runtime-adapter", "--session-id", session_id, "--agent-command", quote_arg(agent_command)),
        "prepare_answer": doctor_command(root, "answer-review", "--session-id", session_id, "--action", "prepare"),
        "run_answer": doctor_command(root, "answer-review", "--session-id", session_id, "--action", "run", "--command", quote_arg(agent_command)),
        "record_answer": doctor_command(root, "answer-review", "--session-id", session_id, "--action", "record", "--answer-file", "/path/to/answer.md"),
        "prepare_execution": doctor_command(root, "execution-review", "--session-id", session_id, "--action", "prepare"),
        "runtime_acceptance": doctor_command(root, "runtime-acceptance", "--session-id", session_id),
    }


def mcp_tool_sequence(session_id: str, *, agent_command: str) -> list[dict[str, Any]]:
    return [
        {"tool": "doctor_agent_preflight", "arguments": {"session_id": session_id, "advance": "clarify", "goal": "<user task>"}},
        {"tool": "doctor_agent_preflight", "arguments": {"session_id": session_id, "advance": "context", "source_scope": "all", "limit": 8}},
        {"tool": "doctor_context_review", "arguments": {"session_id": session_id, "action": "approve", "reason": "context matches intent"}},
        {"tool": "doctor_agent_preflight", "arguments": {"session_id": session_id, "advance": "handoff", "agent_command": agent_command}},
        {"tool": "doctor_answer_review", "arguments": {"session_id": session_id, "action": "prepare"}},
        {"tool": "doctor_answer_review", "arguments": {"session_id": session_id, "action": "run", "command": agent_command}},
        {"tool": "doctor_answer_review", "arguments": {"session_id": session_id, "action": "approve", "reason": "answer matches intent"}},
        {"tool": "doctor_execution_review", "arguments": {"session_id": session_id, "action": "prepare"}},
        {"tool": "doctor_runtime_acceptance", "arguments": {"session_id": session_id}},
    ]


def render_adapter_overview(manifest: dict[str, Any]) -> str:
    lines = [
        "---",
        f"runtime_adapter_version: {manifest['runtime_adapter_version']}",
        f"status: {manifest['status']}",
        f"session_id: {manifest['session_id']}",
        "---",
        "",
        "# Doctor Runtime Adapter",
        "",
        "This package is the stable adapter boundary for Codex++, Warp, Codex CLI, MCP clients, or another local agent. It tells a client which reviewed Doctor files to read and which command advances the current gate.",
        "",
        "## Current Session",
        "",
        f"- Runtime status: `{manifest['runtime_status']}`",
        f"- Current review file: `{manifest.get('current_review_file')}`",
        f"- Review server: `{manifest['review_server_url']}`",
        f"- Agent command: `{manifest['agent_command']}`",
        "",
        "## Files",
        "",
    ]
    for key in [
        "doctor_session_md_path",
        "model_input_md_path",
        "agent_handoff_md_path",
        "answer_packet_md_path",
        "answer_md_path",
        "execution_report_md_path",
        "execution_artifact_index_md_path",
    ]:
        lines.append(f"- {key}: `{manifest['files'].get(key)}`")
    lines.extend(["", "## Entry Points", "", "```bash"])
    lines.extend(str(value) for value in manifest["entrypoints"].values())
    lines.extend(["```", "", "## Adapter Files", ""])
    lines.extend(f"- {key}: `{value}`" for key, value in manifest["adapter_files"].items())
    lines.append("")
    return "\n".join(lines)


def render_target_doc(manifest: dict[str, Any], target: str) -> str:
    labels = {
        "codex-plus": "Codex++",
        "warp": "Warp",
        "codex-cli": "Codex CLI",
        "mcp": "MCP Client",
    }
    label = labels[target]
    lines = [
        f"# Doctor Adapter For {label}",
        "",
        "Read this file as the client-facing contract. The client should not infer Doctor internals; it should use the paths and commands from `adapter_manifest.json`.",
        "",
        "## Required Behavior",
        "",
        "- Show the current review file to the user before advancing a gate.",
        "- Prefer `doctor agent-preflight` / `doctor_agent_preflight` as the default entrypoint instead of calling low-level resolver tools directly.",
        "- Use `agent_handoff.md` / `model_input.md` as the only approved local context payload.",
        "- Use the answer command adapter or record an answer file before execution.",
        "- Record approve/reject decisions through Doctor instead of hiding them in chat history.",
        "",
        "## Current Commands",
        "",
        "```bash",
        manifest["entrypoints"]["inspect"],
        manifest["entrypoints"]["review_server"],
        manifest["entrypoints"]["run_answer"],
        manifest["entrypoints"]["runtime_acceptance"],
        "```",
        "",
    ]
    if target == "mcp":
        lines.extend(["## MCP Tool Sequence", "", "See `mcp_tool_sequence.json`."])
    return "\n".join(lines)


def render_env_script(root: Path, session_id: str, manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"export AGENT_CONTEXT_ROOT={quote_arg(str(root))}",
            f"export DOCTOR_SESSION_ID={quote_arg(session_id)}",
            f"export DOCTOR_ADAPTER_MANIFEST={quote_arg(manifest['adapter_files']['manifest'])}",
            f"export DOCTOR_REVIEW_SERVER_URL={quote_arg(manifest['review_server_url'])}",
            "",
        ]
    )


def render_codex_cli_wrapper(root: Path, session_id: str, manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"AGENT_CONTEXT_ROOT={quote_arg(str(root))}",
            f"DOCTOR_SESSION_ID={quote_arg(session_id)}",
            f"DOCTOR_ADAPTER_MANIFEST={quote_arg(manifest['adapter_files']['manifest'])}",
            "export AGENT_CONTEXT_ROOT DOCTOR_SESSION_ID DOCTOR_ADAPTER_MANIFEST",
            f"doctor session --out {quote_arg(str(root))} --session-id {quote_arg(session_id)} >/dev/null",
            "echo \"Doctor adapter manifest: ${DOCTOR_ADAPTER_MANIFEST}\"",
            "echo \"Review this file before sending model input:\"",
            f"python - <<'PY'\nimport json\nfrom pathlib import Path\nm=json.loads(Path({manifest['adapter_files']['manifest']!r}).read_text())\nprint(m.get('current_review_file') or m['files'].get('doctor_session_md_path'))\nPY",
            "",
        ]
    )


def doctor_command(root: Path, command: str, *args: str) -> str:
    return " ".join(["doctor", command, "--out", quote_arg(str(root)), *args])


def quote_arg(value: str) -> str:
    escaped = value.replace("'", "'\"'\"'")
    return f"'{escaped}'"


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
