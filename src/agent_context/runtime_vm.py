from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .clarify import build_clarification
from .io import write_text


DOCTOR_RUNTIME_VERSION = "0.1"


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


def persist_runtime_session(session: dict[str, Any]) -> None:
    files = session["files"]
    write_text(Path(files["runtime_session_json_path"]), json.dumps(session, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    markdown = render_session_markdown(session)
    write_text(Path(files["runtime_session_md_path"]), markdown)
    write_text(Path(files["doctor_session_md_path"]), markdown)


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
            "Use answer_packet.md with the model, then record the answer.",
            [doctor_command(root, "answer-review", "--session-id", session_id, "--action", "record", "--answer-file", "/path/to/answer.md")],
            ready=False,
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
        "answer_review_json_path": str(session_dir / "answer_review.json"),
        "answer_packet_md_path": str(session_dir / "answer_packet.md"),
        "answer_md_path": str(session_dir / "answer.md"),
        "answer_review_events_jsonl_path": str(session_dir / "answer_review_events.jsonl"),
        "execution_review_json_path": str(session_dir / "execution_review.json"),
        "execution_report_md_path": str(session_dir / "execution_report.md"),
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
        if stage.get("artifacts_dir"):
            lines.append(f"  - Artifacts: `{stage['artifacts_dir']}`")
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
            "  answer_review.json",
            "  answer_packet.md",
            "  answer.md",
            "  answer_review_events.jsonl",
            "  execution_review.json",
            "  execution_report.md",
            "  execution_review_events.jsonl",
            "  artifacts/",
            "```",
            "",
        ]
    )
    return "\n".join(lines)
