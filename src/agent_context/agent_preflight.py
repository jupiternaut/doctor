from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .answer_review import run_answer_review
from .context_review import run_context_review
from .execution_review import run_execution_review
from .io import write_text
from .runtime_adapters import export_runtime_adapter_package
from .runtime_vm import export_runtime_handoff, inspect_runtime_session, start_runtime_session


AGENT_PREFLIGHT_VERSION = "0.1"
AGENT_PREFLIGHT_ADVANCES = {"clarify", "context", "handoff", "answer", "execution"}


def run_agent_preflight(
    out_root: str | Path,
    *,
    advance: str = "clarify",
    goal: str | None = None,
    session_id: str | None = None,
    source_scope: str = "all",
    limit: int = 8,
    mode: str = "fast",
    agent_command: str = "<agent command>",
    review_port: int = 8765,
    image_paths: list[str] | None = None,
    answer_command: str = "",
    answer_text: str = "",
    answer_file: str | Path | None = None,
    execution_command: str = "",
    artifact_file: str | Path | None = None,
    cwd: str | Path | None = None,
    timeout_seconds: int = 120,
    reason: str = "",
) -> dict[str, Any]:
    if advance not in AGENT_PREFLIGHT_ADVANCES:
        raise ValueError(f"unknown agent preflight advance: {advance}")
    root = Path(out_root).expanduser().resolve()
    action_result: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    adapter: dict[str, Any] | None = None

    if advance == "clarify":
        if not goal:
            raise ValueError("goal is required when advance=clarify")
        action_result = start_runtime_session(root, goal, session_id=session_id, mode="standard", image_paths=image_paths)
        resolved_session_id = str(action_result["session_id"])
    elif advance == "context":
        if not session_id:
            raise ValueError("session_id is required when advance=context")
        action_result = run_context_review(
            root,
            action="generate",
            session_id=session_id,
            source_scope=source_scope,
            limit=max(1, int(limit)),
            mode=mode,
        )
        resolved_session_id = session_id
    elif advance == "handoff":
        if not session_id:
            raise ValueError("session_id is required when advance=handoff")
        handoff = export_runtime_handoff(root, session_id)
        adapter = export_runtime_adapter_package(
            root,
            session_id,
            agent_command=agent_command,
            review_port=review_port,
        )
        action_result = {"handoff": summarize_handoff(handoff), "adapter": summarize_adapter(adapter)}
        resolved_session_id = session_id
    elif advance == "answer":
        if not session_id:
            raise ValueError("session_id is required when advance=answer")
        handoff, adapter = ensure_handoff_and_adapter(
            root,
            session_id,
            agent_command=agent_command,
            review_port=review_port,
        )
        action_result = advance_answer_gate(
            root,
            session_id=session_id,
            answer_command=answer_command,
            answer_text=answer_text,
            answer_file=answer_file,
            cwd=cwd,
            timeout_seconds=max(1, int(timeout_seconds)),
            reason=reason,
        )
        resolved_session_id = session_id
    else:
        if not session_id:
            raise ValueError("session_id is required when advance=execution")
        action_result = advance_execution_gate(
            root,
            session_id=session_id,
            execution_command=execution_command,
            artifact_file=artifact_file,
            cwd=cwd,
            timeout_seconds=max(1, int(timeout_seconds)),
            reason=reason,
        )
        resolved_session_id = session_id

    session = inspect_runtime_session(root, resolved_session_id)
    result = build_agent_preflight_result(
        root,
        session,
        advance=advance,
        action_result=action_result,
        handoff=handoff,
        adapter=adapter,
        source_scope=source_scope,
        limit=max(1, int(limit)),
        mode=mode,
    )
    persist_agent_preflight(result)
    return result


def ensure_handoff_and_adapter(
    root: Path,
    session_id: str,
    *,
    agent_command: str,
    review_port: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    session_dir = root / "runtime" / "sessions" / session_id
    context_path = session_dir / "context_review.json"
    if not context_path.exists():
        return None, None
    context_review = json.loads(context_path.read_text(encoding="utf-8"))
    if context_review.get("status") != "approved":
        return None, None
    handoff = None
    adapter = None
    if not (session_dir / "agent_handoff.md").exists():
        handoff = export_runtime_handoff(root, session_id)
    if not (session_dir / "adapters" / "adapter_manifest.json").exists():
        adapter = export_runtime_adapter_package(
            root,
            session_id,
            agent_command=agent_command,
            review_port=review_port,
        )
    return handoff, adapter


def advance_answer_gate(
    root: Path,
    *,
    session_id: str,
    answer_command: str,
    answer_text: str,
    answer_file: str | Path | None,
    cwd: str | Path | None,
    timeout_seconds: int,
    reason: str,
) -> dict[str, Any]:
    if answer_file or answer_text.strip():
        return run_answer_review(
            root,
            action="record",
            session_id=session_id,
            answer_text=answer_text,
            answer_file=answer_file,
            reason=reason,
        )
    if answer_command.strip():
        return run_answer_review(
            root,
            action="run",
            session_id=session_id,
            command=answer_command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            reason=reason,
        )
    session = inspect_runtime_session(root, session_id)
    if session["status"] == "ready_for_answer_prepare":
        return run_answer_review(root, action="prepare", session_id=session_id, reason=reason)
    return {
        "status": session["status"],
        "stage": "answer_review",
        "session_id": session_id,
        "message": session["next"].get("message"),
    }


def advance_execution_gate(
    root: Path,
    *,
    session_id: str,
    execution_command: str,
    artifact_file: str | Path | None,
    cwd: str | Path | None,
    timeout_seconds: int,
    reason: str,
) -> dict[str, Any]:
    if artifact_file:
        return run_execution_review(
            root,
            action="record",
            session_id=session_id,
            artifact_file=artifact_file,
            reason=reason,
        )
    if execution_command.strip():
        return run_execution_review(
            root,
            action="run",
            session_id=session_id,
            command=execution_command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            reason=reason,
        )
    session = inspect_runtime_session(root, session_id)
    if session["status"] == "ready_for_execution_prepare":
        return run_execution_review(root, action="prepare", session_id=session_id, reason=reason)
    return {
        "status": session["status"],
        "stage": "execute_review",
        "session_id": session_id,
        "message": session["next"].get("message"),
    }


def build_agent_preflight_result(
    root: Path,
    session: dict[str, Any],
    *,
    advance: str,
    action_result: dict[str, Any] | None,
    handoff: dict[str, Any] | None,
    adapter: dict[str, Any] | None,
    source_scope: str,
    limit: int,
    mode: str,
) -> dict[str, Any]:
    session_id = str(session["session_id"])
    session_dir = root / "runtime" / "sessions" / session_id
    next_state = session.get("next") or {}
    files = session.get("files") or {}
    json_path = session_dir / "agent_preflight.json"
    md_path = session_dir / "agent_preflight.md"
    result = {
        "agent_preflight_version": AGENT_PREFLIGHT_VERSION,
        "status": session.get("status"),
        "advance": advance,
        "created_at": datetime.now().astimezone().isoformat(),
        "session_id": session_id,
        "out_root": str(root),
        "doctor_access": advance != "clarify",
        "resolver_allowed": advance == "context",
        "index_access_allowed": advance == "context",
        "source_scope": source_scope,
        "limit": limit,
        "mode": mode,
        "review_file": next_state.get("review_file"),
        "next_message": next_state.get("message"),
        "next_commands": next_state.get("commands") or [],
        "files": files,
        "action_result": summarize_action_result(action_result),
        "agent_handoff": summarize_handoff(handoff) if handoff else None,
        "runtime_adapter": summarize_adapter(adapter) if adapter else None,
        "client_contract": client_contract_for(session),
        "agent_preflight_json_path": str(json_path),
        "agent_preflight_md_path": str(md_path),
        "runtime_session": session,
    }
    return result


def summarize_action_result(action_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not action_result:
        return None
    return {
        "status": action_result.get("status"),
        "session_id": action_result.get("session_id"),
        "stage": action_result.get("stage") or action_result.get("started_stage"),
        "context_review_json_path": action_result.get("context_review_json_path"),
        "context_review_md_path": action_result.get("context_review_md_path"),
        "refined_prompt_md_path": action_result.get("refined_prompt_md_path"),
        "answer_review_json_path": action_result.get("answer_review_json_path"),
        "answer_packet_md_path": action_result.get("answer_packet_md_path"),
        "answer_md_path": action_result.get("answer_md_path"),
        "execution_review_json_path": action_result.get("execution_review_json_path"),
        "execution_report_md_path": action_result.get("execution_report_md_path"),
        "artifact_manifest_jsonl_path": action_result.get("artifact_manifest_jsonl_path"),
        "artifact_index_md_path": action_result.get("artifact_index_md_path"),
        "agent_handoff_md_path": (action_result.get("handoff") or {}).get("agent_handoff_md_path"),
        "runtime_adapter_manifest_json_path": (action_result.get("adapter") or {}).get("manifest"),
    }


def summarize_handoff(handoff: dict[str, Any] | None) -> dict[str, Any] | None:
    if not handoff:
        return None
    return {
        "status": handoff.get("status"),
        "agent_handoff_md_path": handoff.get("agent_handoff_md_path"),
        "agent_handoff_json_path": handoff.get("agent_handoff_json_path"),
        "model_input_md_path": handoff.get("model_input_md_path"),
        "answer_packet_md_path": handoff.get("answer_packet_md_path"),
    }


def summarize_adapter(adapter: dict[str, Any] | None) -> dict[str, Any] | None:
    if not adapter:
        return None
    files = adapter.get("adapter_files") or {}
    return {
        "status": adapter.get("status"),
        "targets": adapter.get("targets") or [],
        "manifest": files.get("manifest"),
        "overview": files.get("overview"),
        "codex_cli_wrapper": files.get("codex_cli_wrapper"),
        "mcp_tool_sequence": files.get("mcp_tool_sequence"),
    }


def client_contract_for(session: dict[str, Any]) -> dict[str, Any]:
    status = str(session.get("status") or "")
    next_state = session.get("next") or {}
    files = session.get("files") or {}
    if status == "awaiting_context_generation":
        instruction = "Show refined_prompt.md to the user. Do not query Doctor indexes until the user approves this prompt."
        safe_to_send_model = False
    elif status == "awaiting_context_review":
        instruction = "Show model_input.md to the user. Do not send it to a model until the user approves the context review."
        safe_to_send_model = False
    elif status in {"ready_for_agent_handoff", "ready_for_runtime_adapter", "ready_for_answer_prepare", "awaiting_answer_output"}:
        instruction = "Use only the approved model_input.md or agent_handoff.md as the local evidence payload for the model."
        safe_to_send_model = True
    elif status == "awaiting_answer_review":
        instruction = "Show answer.md to the user. Do not prepare local execution until the user approves the answer."
        safe_to_send_model = False
    elif status in {"ready_for_execution_prepare", "awaiting_execution", "awaiting_execution_review", "execution_rejected"}:
        instruction = "Use the approved answer only for the explicit execution gate. Do not run extra local commands outside Doctor execution review."
        safe_to_send_model = False
    elif status == "complete":
        instruction = "All four Doctor review gates are approved. Use execution artifacts as the final reviewed output."
        safe_to_send_model = False
    else:
        instruction = "Follow the current Doctor review gate before advancing."
        safe_to_send_model = False
    return {
        "instruction": instruction,
        "safe_to_send_model": safe_to_send_model,
        "current_review_file": next_state.get("review_file"),
        "approved_model_input_md_path": files.get("model_input_md_path") if safe_to_send_model else "",
        "agent_handoff_md_path": files.get("agent_handoff_md_path") if safe_to_send_model else "",
        "answer_packet_md_path": files.get("answer_packet_md_path") or "",
        "answer_md_path": files.get("answer_md_path") or "",
        "execution_report_md_path": files.get("execution_report_md_path") or "",
        "execution_artifact_index_md_path": files.get("execution_artifact_index_md_path") or "",
    }


def persist_agent_preflight(result: dict[str, Any]) -> None:
    json_path = Path(str(result["agent_preflight_json_path"]))
    md_path = Path(str(result["agent_preflight_md_path"]))
    write_text(json_path, json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(md_path, render_agent_preflight_markdown(result))


def render_agent_preflight_markdown(result: dict[str, Any]) -> str:
    contract = result.get("client_contract") or {}
    files = result.get("files") or {}
    lines = [
        "---",
        f"agent_preflight_version: {result['agent_preflight_version']}",
        f"status: {result['status']}",
        f"advance: {result['advance']}",
        f"session_id: {result['session_id']}",
        f"doctor_access: {str(result['doctor_access']).lower()}",
        f"resolver_allowed: {str(result['resolver_allowed']).lower()}",
        "---",
        "",
        "# Doctor Agent Preflight",
        "",
        "This file is the default entrypoint for Codex++, Warp, Codex CLI, or MCP clients. It tells the client which Doctor review file to show before any context is sent to a model.",
        "",
        "## Client Contract",
        "",
        f"- Instruction: {contract.get('instruction')}",
        f"- Safe to send model: `{str(contract.get('safe_to_send_model')).lower()}`",
        f"- Current review file: `{contract.get('current_review_file')}`",
        f"- Approved model input: `{contract.get('approved_model_input_md_path')}`",
        f"- Agent handoff: `{contract.get('agent_handoff_md_path')}`",
        f"- Answer packet: `{contract.get('answer_packet_md_path')}`",
        f"- Answer: `{contract.get('answer_md_path')}`",
        f"- Execution report: `{contract.get('execution_report_md_path')}`",
        f"- Execution artifacts: `{contract.get('execution_artifact_index_md_path')}`",
        "",
        "## Runtime Files",
        "",
        f"- Doctor session: `{files.get('doctor_session_md_path')}`",
        f"- Refined prompt: `{files.get('refined_prompt_md_path')}`",
        f"- Model input: `{files.get('model_input_md_path')}`",
        f"- Agent handoff: `{files.get('agent_handoff_md_path')}`",
        f"- Runtime adapter manifest: `{files.get('runtime_adapter_manifest_json_path')}`",
        "",
        "## Next Commands",
        "",
    ]
    commands = result.get("next_commands") or []
    if commands:
        lines.extend(["```bash", *[str(command) for command in commands], "```"])
    else:
        lines.append("- No next command.")
    lines.append("")
    return "\n".join(lines)
