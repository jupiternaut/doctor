from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .clarify import build_clarification
from .io import write_text
from .pack import slugify


DOCTOR_RUNTIME_VERSION = "0.1"
RUNTIME_HANDOFF_VERSION = "0.1"


def start_runtime_session(
    out_root: str | Path,
    goal: str,
    *,
    session_id: str | None = None,
    mode: str = "standard",
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    clarification = build_clarification(root, goal, session_id=session_id, mode=mode)
    result = inspect_runtime_session(root, clarification["session_id"], write_report=False)
    result["started_stage"] = "clarify"
    result["original_goal"] = goal
    result["normalized_goal"] = clarification.get("normalized_goal")
    persist_runtime_session(result)
    return result


def inspect_runtime_session(
    out_root: str | Path,
    session_id: str,
    *,
    write_report: bool = True,
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    session_dir = root / "runtime" / "sessions" / session_id
    if not session_dir.exists():
        raise FileNotFoundError(f"runtime session not found: {session_dir}")

    stages = build_stage_states(root, session_id)
    next_state = determine_next_state(root, session_id, stages)
    files = runtime_file_contract(root, session_id, stages)
    result: dict[str, Any] = {
        "doctor_runtime_version": DOCTOR_RUNTIME_VERSION,
        "status": next_state["status"],
        "ready_for_next_stage": next_state["ready_for_next_stage"],
        "created_at": datetime.now().astimezone().isoformat(),
        "session_id": session_id,
        "out_root": str(root),
        "session_dir": str(session_dir),
        "stages": stages,
        "next": next_state,
        "files": files,
    }

    if write_report:
        persist_runtime_session(result)
    return result


def run_runtime_vm_acceptance(out_root: str | Path, session_id: str) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    session = inspect_runtime_session(root, session_id)
    checks = runtime_acceptance_checks(session)
    ready = all(check["status"] == "ok" for check in checks if check.get("required_for_complete"))
    status = "complete" if ready else session["status"]
    now = datetime.now().astimezone()
    report_id = f"runtime-vm-acceptance-{slugify(session_id)}-{now.strftime('%Y%m%d%H%M%S%f')}"
    reports_dir = root / "reports"
    json_path = reports_dir / f"{report_id}.json"
    md_path = reports_dir / f"{report_id}.md"
    latest_json_path = reports_dir / "runtime-vm-acceptance-latest.json"
    latest_md_path = reports_dir / "runtime-vm-acceptance-latest.md"
    report = {
        "doctor_runtime_acceptance_version": DOCTOR_RUNTIME_VERSION,
        "status": status,
        "ready": ready,
        "created_at": now.isoformat(),
        "session_id": session_id,
        "out_root": str(root),
        "session": session,
        "checks": checks,
        "mcp_tools": [
            "doctor_run",
            "doctor_agent_preflight",
            "doctor_session",
            "doctor_runtime_acceptance",
            "doctor_runtime_handoff",
            "doctor_runtime_adapter",
            "doctor_context_review",
            "doctor_answer_review",
            "doctor_execution_review",
        ],
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    markdown = render_runtime_acceptance_markdown(report)
    write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(md_path, markdown)
    write_text(latest_json_path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(latest_md_path, markdown)
    return report


def export_runtime_handoff(out_root: str | Path, session_id: str) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    session_dir = root / "runtime" / "sessions" / session_id
    context_review_path = session_dir / "context_review.json"
    if not context_review_path.exists():
        raise FileNotFoundError(f"context_review.json not found: {context_review_path}")
    context_review = json.loads(context_review_path.read_text(encoding="utf-8"))
    if context_review.get("status") != "approved":
        raise ValueError("context_review must be approved before exporting an agent handoff")
    preflight = context_review.get("preflight") or {}
    model_input_path = preflight.get("model_input_md_path")
    if not model_input_path or not Path(str(model_input_path)).exists():
        raise ValueError("approved context_review does not reference an existing model_input.md")

    files = runtime_file_contract(root, session_id, build_stage_states(root, session_id))
    handoff_md_path = Path(files["agent_handoff_md_path"])
    handoff_json_path = Path(files["agent_handoff_json_path"])
    now = datetime.now().astimezone().isoformat()
    handoff = {
        "runtime_handoff_version": RUNTIME_HANDOFF_VERSION,
        "stage": "agent_handoff",
        "status": "ready_for_agent",
        "created_at": now,
        "session_id": session_id,
        "out_root": str(root),
        "adapter_targets": ["Codex++", "Warp", "Doctor"],
        "context_review_json_path": str(context_review_path),
        "model_input_md_path": str(model_input_path),
        "context_md_path": preflight.get("context_md_path"),
        "sources_jsonl_path": preflight.get("sources_jsonl_path"),
        "manifest_json_path": preflight.get("manifest_json_path"),
        "resolution_plan_json_path": preflight.get("resolution_plan_json_path"),
        "answer_packet_md_path": files["answer_packet_md_path"],
        "answer_md_path": files["answer_md_path"],
        "agent_handoff_md_path": str(handoff_md_path),
        "agent_handoff_json_path": str(handoff_json_path),
        "instructions": [
            "Use model_input.md as the only approved local-evidence payload for this session.",
            "Do not read additional local sources unless the user starts a new Doctor review gate.",
            "Keep evidence, inference, limitations, and next actions separate in the answer.",
            "After producing an answer, record it through doctor answer-review before local execution.",
        ],
        "commands": {
            "inspect_session": doctor_command(root, "session", "--session-id", session_id),
            "record_answer": doctor_command(root, "answer-review", "--session-id", session_id, "--action", "record", "--answer-file", "/path/to/answer.md"),
            "open_review_server": doctor_command(root, "runtime-review-server", "--session-id", session_id, "--port", "8765"),
            "prepare_answer_packet": doctor_command(root, "answer-review", "--session-id", session_id, "--action", "prepare"),
        },
    }
    write_text(handoff_json_path, json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(handoff_md_path, render_runtime_handoff_markdown(handoff))
    handoff["runtime_session"] = inspect_runtime_session(root, session_id)
    return handoff


def persist_runtime_session(session: dict[str, Any]) -> None:
    files = session["files"]
    write_text(Path(files["runtime_session_json_path"]), json.dumps(session, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    markdown = render_session_markdown(session)
    write_text(Path(files["runtime_session_md_path"]), markdown)
    write_text(Path(files["doctor_session_md_path"]), markdown)


def runtime_acceptance_checks(session: dict[str, Any]) -> list[dict[str, Any]]:
    files = session["files"]
    stages = {stage["name"]: stage for stage in session["stages"]}
    context = stages["context_review"]
    answer = stages["answer_review"]
    execution = stages["execution_review"]
    return [
        file_check("session_entrypoint", "DOCTOR_SESSION.md exists", files["doctor_session_md_path"], required=True),
        file_check("runtime_session_json", "runtime_session.json exists", files["runtime_session_json_path"], required=True),
        file_check("refined_prompt", "Stage 1 refined prompt exists", files["refined_prompt_md_path"], required=True),
        bool_check(
            "clarify_no_index",
            "Stage 1 did not access Doctor indexes or resolver",
            bool(
                stages["clarify"]["exists"]
                and stages["clarify"].get("doctor_access") is False
                and stages["clarify"].get("resolver_called") is False
                and stages["clarify"].get("index_access") is False
            ),
            required=True,
            evidence=stages["clarify"]["state_path"],
        ),
        bool_check(
            "context_model_input",
            "Stage 2 generated a reviewable model_input.md",
            bool(context["exists"] and context.get("model_input_md_path") and Path(str(context["model_input_md_path"])).exists()),
            required=True,
            evidence=context.get("model_input_md_path") or context["state_path"],
        ),
        bool_check(
            "context_approved",
            "Stage 2 context payload is approved",
            context["status"] == "approved",
            required=True,
            evidence=context["state_path"],
        ),
        file_check(
            "agent_handoff",
            "Approved context is exported as a Codex++/Warp/Doctor handoff",
            files["agent_handoff_md_path"],
            required=True,
        ),
        file_check(
            "runtime_adapter_package",
            "Runtime adapter package exists for Codex++/Warp/Codex CLI/MCP clients",
            files["runtime_adapter_manifest_json_path"],
            required=True,
        ),
        bool_check(
            "answer_packet",
            "Stage 3 answer packet exists",
            bool(answer["exists"] and Path(str(answer["review_path"])).exists()),
            required=True,
            evidence=answer["review_path"],
        ),
        bool_check(
            "answer_recorded",
            "Stage 3 recorded an answer for review",
            bool(answer.get("answer_md_path") and Path(str(answer["answer_md_path"])).exists()),
            required=True,
            evidence=answer.get("answer_md_path") or answer["state_path"],
        ),
        bool_check(
            "answer_approved",
            "Stage 3 answer is approved",
            answer["status"] == "approved",
            required=True,
            evidence=answer["state_path"],
        ),
        bool_check(
            "execution_report",
            "Stage 4 execution report exists",
            bool(execution["exists"] and Path(str(execution["review_path"])).exists()),
            required=True,
            evidence=execution["review_path"],
        ),
        bool_check(
            "execution_artifact",
            "Stage 4 has a command run or external artifact",
            bool((execution.get("command_count") or 0) > 0 or (execution.get("external_artifact_count") or 0) > 0),
            required=True,
            evidence=execution.get("artifacts_dir") or execution["state_path"],
        ),
        file_check(
            "execution_artifact_manifest",
            "Stage 4 indexed produced artifacts in a unified manifest",
            files["execution_artifact_manifest_jsonl_path"],
            required=True,
        ),
        bool_check(
            "execution_approved",
            "Stage 4 execution output is approved",
            execution["status"] == "approved",
            required=True,
            evidence=execution["state_path"],
        ),
    ]


def file_check(check_id: str, description: str, path: str | None, *, required: bool) -> dict[str, Any]:
    exists = bool(path and Path(path).exists())
    return bool_check(check_id, description, exists, required=required, evidence=path)


def bool_check(
    check_id: str,
    description: str,
    ok: bool,
    *,
    required: bool,
    evidence: str | None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "description": description,
        "status": "ok" if ok else "missing",
        "required_for_complete": required,
        "evidence": evidence,
    }


def build_stage_states(root: Path, session_id: str) -> list[dict[str, Any]]:
    session_dir = root / "runtime" / "sessions" / session_id
    clarify = load_state(session_dir / "clarify.json")
    context = load_state(session_dir / "context_review.json")
    answer = load_state(session_dir / "answer_review.json")
    execution = load_state(session_dir / "execution_review.json")
    return [
        {
            "name": "clarify",
            "label": "1. clarify/refine",
            "exists": clarify is not None,
            "status": state_status(clarify),
            "state_path": str(session_dir / "clarify.json"),
            "review_path": str(session_dir / "refined_prompt.md"),
            "doctor_access": clarify.get("doctor_access") if clarify else None,
            "resolver_called": clarify.get("resolver_called") if clarify else None,
            "index_access": clarify.get("index_access") if clarify else None,
            "intent": clarify.get("intent") if clarify else None,
            "source_scope_hint": clarify.get("source_scope_hint") if clarify else None,
        },
        {
            "name": "context_review",
            "label": "2. resolve/review",
            "exists": context is not None,
            "status": state_status(context),
            "state_path": str(session_dir / "context_review.json"),
            "review_path": str(session_dir / "context_review.md"),
            "model_input_md_path": ((context.get("preflight") or {}).get("model_input_md_path") if context else None),
            "context_md_path": ((context.get("preflight") or {}).get("context_md_path") if context else None),
            "sources_jsonl_path": ((context.get("preflight") or {}).get("sources_jsonl_path") if context else None),
        },
        {
            "name": "answer_review",
            "label": "3. answer/review",
            "exists": answer is not None,
            "status": state_status(answer),
            "state_path": str(session_dir / "answer_review.json"),
            "review_path": str(session_dir / "answer_packet.md"),
            "answer_md_path": answer.get("answer_md_path") if answer else str(session_dir / "answer.md"),
            "answer_runs_dir": answer.get("answer_runs_dir") if answer else str(session_dir / "answer_runs"),
            "answer_run_count": len(answer.get("answer_runs") or []) if answer else 0,
            "model_input_md_path": answer.get("model_input_md_path") if answer else None,
        },
        {
            "name": "execution_review",
            "label": "4. execute/review",
            "exists": execution is not None,
            "status": state_status(execution),
            "state_path": str(session_dir / "execution_review.json"),
            "review_path": str(session_dir / "execution_report.md"),
            "artifacts_dir": execution.get("artifacts_dir") if execution else str(session_dir / "artifacts"),
            "artifact_manifest_jsonl_path": (
                execution.get("artifact_manifest_jsonl_path") if execution else str(session_dir / "execution_artifacts.jsonl")
            ),
            "artifact_index_md_path": (
                execution.get("artifact_index_md_path") if execution else str(session_dir / "execution_artifacts.md")
            ),
            "command_count": len(execution.get("commands") or []) if execution else 0,
            "external_artifact_count": len(execution.get("external_artifacts") or []) if execution else 0,
        },
    ]


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def state_status(state: dict[str, Any] | None) -> str:
    if state is None:
        return "missing"
    return str(state.get("status") or "unknown")


def determine_next_state(root: Path, session_id: str, stages: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {stage["name"]: stage for stage in stages}
    clarify = by_name["clarify"]
    context = by_name["context_review"]
    answer = by_name["answer_review"]
    execution = by_name["execution_review"]

    if not clarify["exists"]:
        return next_state(
            "not_started",
            "Run no-index clarification first.",
            [doctor_command(root, "run", "--goal", quote_goal("<user goal>"), "--session-id", session_id)],
            ready=True,
            review_file=None,
        )
    if not context["exists"]:
        return next_state(
            "awaiting_context_generation",
            "Review refined_prompt.md, then generate the Doctor model input.",
            [doctor_command(root, "context-review", "--session-id", session_id, "--action", "generate", "--source-scope", "all", "--limit", "8")],
            ready=True,
            review_file=clarify["review_path"],
        )
    if context["status"] == "rejected":
        return next_state(
            "context_rejected",
            "Regenerate the context payload with a better scope, limit, or reason.",
            [doctor_command(root, "context-review", "--session-id", session_id, "--action", "regenerate", "--source-scope", "all", "--limit", "8", "--reason", quote_goal("try better sources"))],
            ready=True,
            review_file=context.get("model_input_md_path") or context["review_path"],
        )
    if context["status"] != "approved":
        return next_state(
            "awaiting_context_review",
            "Review model_input.md before any model or agent consumes it.",
            [
                doctor_command(root, "context-review", "--session-id", session_id, "--action", "approve", "--reason", quote_goal("context matches intent")),
                doctor_command(root, "context-review", "--session-id", session_id, "--action", "reject", "--reason", quote_goal("wrong sources")),
            ],
            ready=False,
            review_file=context.get("model_input_md_path") or context["review_path"],
        )
    handoff_path = root / "runtime" / "sessions" / session_id / "agent_handoff.md"
    if not handoff_path.exists():
        return next_state(
            "ready_for_agent_handoff",
            "Export the approved context as a Codex++/Warp/Doctor handoff before any agent consumes it.",
            [doctor_command(root, "runtime-handoff", "--session-id", session_id)],
            ready=True,
            review_file=context.get("model_input_md_path") or context["review_path"],
        )
    adapter_path = root / "runtime" / "sessions" / session_id / "adapters" / "adapter_manifest.json"
    if not adapter_path.exists() and not answer["exists"]:
        return next_state(
            "ready_for_runtime_adapter",
            "Export the Doctor runtime adapter package for Codex++, Warp, Codex CLI, or MCP clients.",
            [doctor_command(root, "runtime-adapter", "--session-id", session_id)],
            ready=True,
            review_file=str(handoff_path),
        )
    if not answer["exists"]:
        return next_state(
            "ready_for_answer_prepare",
            "Prepare the approved context packet for Codex++, Warp, or Doctor.",
            [doctor_command(root, "answer-review", "--session-id", session_id, "--action", "prepare")],
            ready=True,
            review_file=context.get("model_input_md_path") or context["review_path"],
        )
    if answer["status"] == "awaiting_answer":
        return next_state(
            "awaiting_answer_output",
            "Use answer_packet.md with a model or local answer command, then review the answer.",
            [
                doctor_command(root, "answer-review", "--session-id", session_id, "--action", "run", "--command", quote_goal("<agent command>")),
                doctor_command(root, "answer-review", "--session-id", session_id, "--action", "record", "--answer-file", "/path/to/answer.md"),
            ],
            ready=False,
            review_file=answer["review_path"],
        )
    if answer["status"] == "answer_failed":
        return next_state(
            "answer_failed",
            "The local answer command failed or produced no answer. Rerun it or record an answer manually.",
            [
                doctor_command(root, "answer-review", "--session-id", session_id, "--action", "run", "--command", quote_goal("<agent command>")),
                doctor_command(root, "answer-review", "--session-id", session_id, "--action", "record", "--answer-file", "/path/to/revised-answer.md"),
            ],
            ready=True,
            review_file=answer["review_path"],
        )
    if answer["status"] == "pending_review":
        return next_state(
            "awaiting_answer_review",
            "Review the recorded answer before local execution.",
            [
                doctor_command(root, "answer-review", "--session-id", session_id, "--action", "approve", "--reason", quote_goal("answer matches intent")),
                doctor_command(root, "answer-review", "--session-id", session_id, "--action", "reject", "--reason", quote_goal("answer needs revision")),
            ],
            ready=False,
            review_file=answer.get("answer_md_path") or answer["review_path"],
        )
    if answer["status"] == "rejected":
        return next_state(
            "answer_rejected",
            "Record a revised answer, then review it again.",
            [doctor_command(root, "answer-review", "--session-id", session_id, "--action", "record", "--answer-file", "/path/to/revised-answer.md")],
            ready=True,
            review_file=answer.get("answer_md_path") or answer["review_path"],
        )
    if answer["status"] != "approved":
        return next_state("answer_blocked", "Answer stage is not approved.", [], ready=False, review_file=answer["review_path"])
    if not execution["exists"]:
        return next_state(
            "ready_for_execution_prepare",
            "Prepare the local execution review envelope.",
            [doctor_command(root, "execution-review", "--session-id", session_id, "--action", "prepare")],
            ready=True,
            review_file=answer.get("answer_md_path") or answer["review_path"],
        )
    if execution["status"] == "awaiting_execution":
        return next_state(
            "awaiting_execution",
            "Run an explicit local command or record an external artifact.",
            [doctor_command(root, "execution-review", "--session-id", session_id, "--action", "run", "--command", quote_goal("python scripts/build_report.py"), "--cwd", str(root))],
            ready=True,
            review_file=execution["review_path"],
        )
    if execution["status"] in {"executed", "execution_failed", "pending_review"}:
        return next_state(
            "awaiting_execution_review",
            "Review the generated artifacts and approve or reject the execution output.",
            [
                doctor_command(root, "execution-review", "--session-id", session_id, "--action", "approve", "--reason", quote_goal("artifacts are acceptable")),
                doctor_command(root, "execution-review", "--session-id", session_id, "--action", "reject", "--reason", quote_goal("artifacts need revision")),
            ],
            ready=False,
            review_file=execution["review_path"],
        )
    if execution["status"] == "rejected":
        return next_state(
            "execution_rejected",
            "Run a revised command or record a revised artifact.",
            [doctor_command(root, "execution-review", "--session-id", session_id, "--action", "run", "--command", quote_goal("python scripts/build_report.py"), "--cwd", str(root))],
            ready=True,
            review_file=execution["review_path"],
        )
    if execution["status"] == "approved":
        return next_state("complete", "All four review gates are approved.", [], ready=False, review_file=execution["review_path"])
    return next_state("unknown", "Session state is not recognized.", [], ready=False, review_file=None)


def next_state(
    status: str,
    message: str,
    commands: list[str],
    *,
    ready: bool,
    review_file: str | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "ready_for_next_stage": ready,
        "review_file": review_file,
        "commands": commands,
    }


def doctor_command(root: Path, command: str, *args: str) -> str:
    parts = ["doctor", command, "--out", str(root), *args]
    return " ".join(parts)


def quote_goal(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def runtime_file_contract(root: Path, session_id: str, stages: list[dict[str, Any]]) -> dict[str, Any]:
    session_dir = root / "runtime" / "sessions" / session_id
    by_name = {stage["name"]: stage for stage in stages}
    return {
        "session_dir": str(session_dir),
        "doctor_session_md_path": str(session_dir / "DOCTOR_SESSION.md"),
        "runtime_session_json_path": str(session_dir / "runtime_session.json"),
        "runtime_session_md_path": str(session_dir / "runtime_session.md"),
        "clarify_json_path": str(session_dir / "clarify.json"),
        "refined_prompt_md_path": str(session_dir / "refined_prompt.md"),
        "context_review_json_path": str(session_dir / "context_review.json"),
        "context_review_md_path": str(session_dir / "context_review.md"),
        "context_review_events_jsonl_path": str(session_dir / "context_review_events.jsonl"),
        "model_input_md_path": by_name["context_review"].get("model_input_md_path"),
        "context_md_path": by_name["context_review"].get("context_md_path"),
        "sources_jsonl_path": by_name["context_review"].get("sources_jsonl_path"),
        "agent_handoff_json_path": str(session_dir / "agent_handoff.json"),
        "agent_handoff_md_path": str(session_dir / "agent_handoff.md"),
        "runtime_adapters_dir": str(session_dir / "adapters"),
        "runtime_adapter_manifest_json_path": str(session_dir / "adapters" / "adapter_manifest.json"),
        "runtime_adapter_overview_md_path": str(session_dir / "adapters" / "DOCTOR_RUNTIME_ADAPTER.md"),
        "answer_review_json_path": str(session_dir / "answer_review.json"),
        "answer_packet_md_path": str(session_dir / "answer_packet.md"),
        "answer_md_path": str(session_dir / "answer.md"),
        "answer_runs_dir": str(session_dir / "answer_runs"),
        "answer_review_events_jsonl_path": str(session_dir / "answer_review_events.jsonl"),
        "execution_review_json_path": str(session_dir / "execution_review.json"),
        "execution_report_md_path": str(session_dir / "execution_report.md"),
        "execution_artifact_manifest_jsonl_path": str(session_dir / "execution_artifacts.jsonl"),
        "execution_artifact_index_md_path": str(session_dir / "execution_artifacts.md"),
        "execution_review_events_jsonl_path": str(session_dir / "execution_review_events.jsonl"),
        "artifacts_dir": str(session_dir / "artifacts"),
    }


def render_session_markdown(session: dict[str, Any]) -> str:
    lines = [
        "---",
        f"doctor_runtime_version: {session['doctor_runtime_version']}",
        f"status: {session['status']}",
        f"session_id: {session['session_id']}",
        "---",
        "",
        "# Doctor Runtime Session",
        "",
        "This file is the Docker-like session entrypoint for the local macOS context runtime. It shows which stage is active, which file should be reviewed, and which command advances the session.",
        "",
        "## Current State",
        "",
        f"- Status: `{session['status']}`",
        f"- Ready for next stage: `{str(session['ready_for_next_stage']).lower()}`",
        f"- Message: {session['next']['message']}",
        f"- Review file: `{session['next'].get('review_file')}`",
        "",
        f"- Agent handoff: `{session['files']['agent_handoff_md_path']}`",
        f"- Runtime adapter: `{session['files']['runtime_adapter_manifest_json_path']}`",
        "",
        "## Stages",
        "",
    ]
    for stage in session["stages"]:
        lines.extend(
            [
                f"- {stage['label']}: `{stage['status']}`",
                f"  - State: `{stage['state_path']}`",
                f"  - Review: `{stage['review_path']}`",
            ]
        )
        if stage.get("model_input_md_path"):
            lines.append(f"  - Model input: `{stage['model_input_md_path']}`")
        if stage.get("answer_runs_dir"):
            lines.append(f"  - Answer runs: `{stage['answer_runs_dir']}`")
        if stage.get("artifacts_dir"):
            lines.append(f"  - Artifacts: `{stage['artifacts_dir']}`")
        if stage.get("artifact_index_md_path"):
            lines.append(f"  - Artifact index: `{stage['artifact_index_md_path']}`")
    lines.extend(["", "## Next Commands", ""])
    commands = session["next"].get("commands") or []
    if commands:
        lines.append("```bash")
        lines.extend(commands)
        lines.append("```")
    else:
        lines.append("_No next command. The session is complete or blocked._")
    lines.extend(
        [
            "",
            "## Directory Contract",
            "",
            "```text",
            "runtime/sessions/<session-id>/",
            "  DOCTOR_SESSION.md",
            "  runtime_session.json",
            "  runtime_session.md",
            "  clarify.json",
            "  refined_prompt.md",
            "  context_review.json",
            "  context_review.md",
            "  context_review_events.jsonl",
            "  agent_handoff.json",
            "  agent_handoff.md",
            "  adapters/",
            "  answer_review.json",
            "  answer_packet.md",
            "  answer.md",
            "  answer_runs/",
            "  answer_review_events.jsonl",
            "  execution_review.json",
            "  execution_report.md",
            "  execution_artifacts.jsonl",
            "  execution_artifacts.md",
            "  execution_review_events.jsonl",
            "  artifacts/",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_runtime_handoff_markdown(handoff: dict[str, Any]) -> str:
    lines = [
        "---",
        f"runtime_handoff_version: {handoff['runtime_handoff_version']}",
        f"stage: {handoff['stage']}",
        f"status: {handoff['status']}",
        f"session_id: {handoff['session_id']}",
        "---",
        "",
        "# Doctor Agent Handoff",
        "",
        "This is the approved context handoff for Codex++, Warp, or Doctor. It connects the reviewed `model_input.md` to the answer review gate.",
        "",
        "## Approved Payload",
        "",
        f"- Model input: `{handoff['model_input_md_path']}`",
        f"- Context: `{handoff.get('context_md_path')}`",
        f"- Sources: `{handoff.get('sources_jsonl_path')}`",
        f"- Manifest: `{handoff.get('manifest_json_path')}`",
        f"- Resolution plan: `{handoff.get('resolution_plan_json_path')}`",
        "",
        "## Rules For The Agent",
        "",
    ]
    lines.extend(f"- {item}" for item in handoff["instructions"])
    lines.extend(
        [
            "",
            "## Adapter Targets",
            "",
        ]
    )
    lines.extend(f"- `{target}`" for target in handoff["adapter_targets"])
    lines.extend(
        [
            "",
            "## Commands",
            "",
            "```bash",
        ]
    )
    lines.extend(str(command) for command in handoff["commands"].values())
    lines.extend(
        [
            "```",
            "",
            "## Next Step",
            "",
            "Give the approved `model_input.md` to the model, then record the answer with the `record_answer` command above.",
            "",
        ]
    )
    return "\n".join(lines)


def render_runtime_acceptance_markdown(report: dict[str, Any]) -> str:
    session = report["session"]
    lines = [
        "---",
        f"doctor_runtime_acceptance_version: {report['doctor_runtime_acceptance_version']}",
        f"status: {report['status']}",
        f"ready: {str(report['ready']).lower()}",
        f"session_id: {report['session_id']}",
        "---",
        "",
        "# Doctor Runtime VM Acceptance",
        "",
        "This report is the GitHub handoff for one Doctor runtime session. It verifies the four review gates from current files instead of inferring completion from intent.",
        "",
        "## Result",
        "",
        f"- Status: `{report['status']}`",
        f"- Ready: `{str(report['ready']).lower()}`",
        f"- Session: `{report['session_id']}`",
        f"- Session entrypoint: `{session['files']['doctor_session_md_path']}`",
        f"- Current review file: `{session['next'].get('review_file')}`",
        f"- Next message: {session['next'].get('message')}",
        "",
        "## Checks",
        "",
    ]
    for check in report["checks"]:
        lines.append(f"- `{check['status']}` {check['id']}: {check['description']} (`{check.get('evidence')}`)")
    lines.extend(
        [
            "",
            "## MCP Surface",
            "",
        ]
    )
    lines.extend(f"- `{tool}`" for tool in report["mcp_tools"])
    lines.extend(["", "## Next Commands", ""])
    commands = session["next"].get("commands") or []
    if commands:
        lines.append("```bash")
        lines.extend(commands)
        lines.append("```")
    else:
        lines.append("_No next command._")
    lines.extend(
        [
            "",
            "## Report Files",
            "",
            f"- JSON: `{report['json_path']}`",
            f"- Markdown: `{report['md_path']}`",
            f"- Latest JSON: `{report['latest_json_path']}`",
            f"- Latest Markdown: `{report['latest_md_path']}`",
            "",
        ]
    )
    return "\n".join(lines)
