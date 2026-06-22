from __future__ import annotations

import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import append_jsonl, ensure_dir, write_text


EXECUTION_REVIEW_VERSION = "0.1"
EXECUTION_REVIEW_ACTIONS = {"prepare", "run", "record", "approve", "reject"}


def run_execution_review(
    out_root: str | Path,
    *,
    action: str,
    session_id: str,
    command: str = "",
    cwd: str | Path | None = None,
    timeout_seconds: int = 120,
    artifact_file: str | Path | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if action not in EXECUTION_REVIEW_ACTIONS:
        raise ValueError(f"unknown execution review action: {action}")
    if not session_id:
        raise ValueError("session_id is required")
    root = Path(out_root).expanduser().resolve()
    if action == "prepare":
        return prepare_execution_review(root, session_id=session_id, reason=reason)
    if action == "run":
        return run_execution_command(
            root,
            session_id=session_id,
            command=command,
            cwd=cwd,
            timeout_seconds=max(1, int(timeout_seconds)),
            reason=reason,
        )
    if action == "record":
        return record_execution_artifact(root, session_id=session_id, artifact_file=artifact_file, reason=reason)
    return record_execution_decision(root, action=action, session_id=session_id, reason=reason)


def prepare_execution_review(root: Path, *, session_id: str, reason: str) -> dict[str, Any]:
    answer_review = load_answer_review(root, session_id)
    if answer_review.get("status") != "approved":
        raise ValueError("answer_review must be approved before preparing execution")
    session_dir = ensure_dir(root / "runtime" / "sessions" / session_id)
    artifacts_dir = ensure_dir(session_dir / "artifacts")
    review = {
        "execution_review_version": EXECUTION_REVIEW_VERSION,
        "stage": "execute_review",
        "status": "awaiting_execution",
        "action": "prepare",
        "created_at": datetime.now().astimezone().isoformat(),
        "session_id": session_id,
        "reason": reason,
        "answer_review_json_path": str(session_dir / "answer_review.json"),
        "answer_packet_md_path": answer_review.get("answer_packet_md_path"),
        "answer_md_path": answer_review.get("answer_md_path"),
        "artifacts_dir": str(artifacts_dir),
        "execution_review_json_path": str(session_dir / "execution_review.json"),
        "execution_report_md_path": str(session_dir / "execution_report.md"),
        "events_jsonl_path": str(session_dir / "execution_review_events.jsonl"),
        "global_feedback_jsonl_path": str(root / "feedback" / "execution_review_feedback.jsonl"),
        "commands": [],
        "external_artifacts": [],
    }
    persist_execution_review(root, review, event_action="prepare", event_reason=reason)
    return review


def run_execution_command(
    root: Path,
    *,
    session_id: str,
    command: str,
    cwd: str | Path | None,
    timeout_seconds: int,
    reason: str,
) -> dict[str, Any]:
    if not command.strip():
        raise ValueError("command is required for execution run")
    review = ensure_execution_prepared(root, session_id)
    run_id = datetime.now().strftime("run-%Y%m%d%H%M%S%f")
    artifacts_dir = ensure_dir(Path(str(review["artifacts_dir"])))
    cwd_path = Path(cwd).expanduser().resolve() if cwd else root
    if not cwd_path.exists() or not cwd_path.is_dir():
        raise FileNotFoundError(f"execution cwd not found: {cwd_path}")
    argv = shlex.split(command)
    if not argv:
        raise ValueError("command parsed to no argv")

    started_at = datetime.now().astimezone()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = decode_timeout_output(exc.stdout)
        stderr = decode_timeout_output(exc.stderr) or f"command timed out after {timeout_seconds} seconds"
    finished_at = datetime.now().astimezone()

    stdout_path = artifacts_dir / f"{run_id}.stdout.txt"
    stderr_path = artifacts_dir / f"{run_id}.stderr.txt"
    result_path = artifacts_dir / f"{run_id}.json"
    write_text(stdout_path, stdout)
    write_text(stderr_path, stderr)
    command_record = {
        "run_id": run_id,
        "command": command,
        "argv": argv,
        "cwd": str(cwd_path),
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "returncode": returncode,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "result_json_path": str(result_path),
        "reason": reason,
    }
    write_text(result_path, json.dumps(command_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    review["commands"].append(command_record)
    review["status"] = "executed" if returncode == 0 and not timed_out else "execution_failed"
    review["action"] = "run"
    review["last_run_id"] = run_id
    review["last_returncode"] = returncode
    review["last_timed_out"] = timed_out
    persist_execution_review(root, review, event_action="run", event_reason=reason)
    return review


def record_execution_artifact(
    root: Path,
    *,
    session_id: str,
    artifact_file: str | Path | None,
    reason: str,
) -> dict[str, Any]:
    if not artifact_file:
        raise ValueError("artifact_file is required for record")
    review = ensure_execution_prepared(root, session_id)
    path = Path(artifact_file).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"artifact file not found: {path}")
    record = {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "recorded_at": datetime.now().astimezone().isoformat(),
        "reason": reason,
    }
    review["external_artifacts"].append(record)
    review["status"] = "pending_review"
    review["action"] = "record"
    persist_execution_review(root, review, event_action="record", event_reason=reason)
    return review


def record_execution_decision(root: Path, *, action: str, session_id: str, reason: str) -> dict[str, Any]:
    review = load_execution_review(root, session_id)
    if not review.get("commands") and not review.get("external_artifacts"):
        raise ValueError("execution output must be recorded before approve/reject")
    review["status"] = "approved" if action == "approve" else "rejected"
    review["action"] = action
    review["last_review_action"] = action
    review["last_review_reason"] = reason
    review["last_reviewed_at"] = datetime.now().astimezone().isoformat()
    persist_execution_review(root, review, event_action=action, event_reason=reason)
    return review


def ensure_execution_prepared(root: Path, session_id: str) -> dict[str, Any]:
    path = root / "runtime" / "sessions" / session_id / "execution_review.json"
    if path.exists():
        review = json.loads(path.read_text(encoding="utf-8"))
        answer_review = load_answer_review(root, session_id)
        if answer_review.get("status") != "approved":
            raise ValueError("answer_review must remain approved before execution")
        return review
    return prepare_execution_review(root, session_id=session_id, reason="auto-prepare before execution")


def load_answer_review(root: Path, session_id: str) -> dict[str, Any]:
    path = root / "runtime" / "sessions" / session_id / "answer_review.json"
    if not path.exists():
        raise FileNotFoundError(f"answer_review.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_execution_review(root: Path, session_id: str) -> dict[str, Any]:
    path = root / "runtime" / "sessions" / session_id / "execution_review.json"
    if not path.exists():
        raise FileNotFoundError(f"execution_review.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def persist_execution_review(root: Path, review: dict[str, Any], *, event_action: str, event_reason: str) -> None:
    write_text(Path(str(review["execution_review_json_path"])), json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(Path(str(review["execution_report_md_path"])), render_execution_report_markdown(review))
    event = execution_review_event(review, action=event_action, reason=event_reason)
    append_jsonl(Path(str(review["events_jsonl_path"])), event)
    if event_action in {"approve", "reject"}:
        append_jsonl(root / "feedback" / "execution_review_feedback.jsonl", event)


def execution_review_event(review: dict[str, Any], *, action: str, reason: str) -> dict[str, Any]:
    return {
        "execution_review_version": EXECUTION_REVIEW_VERSION,
        "timestamp": datetime.now().astimezone().isoformat(),
        "session_id": review.get("session_id"),
        "stage": "execute_review",
        "action": action,
        "status": review.get("status"),
        "reason": reason,
        "artifacts_dir": review.get("artifacts_dir"),
        "execution_report_md_path": review.get("execution_report_md_path"),
        "last_run_id": review.get("last_run_id"),
        "last_returncode": review.get("last_returncode"),
    }


def render_execution_report_markdown(review: dict[str, Any]) -> str:
    lines = [
        "---",
        f"execution_review_version: {review['execution_review_version']}",
        f"stage: {review['stage']}",
        f"status: {review['status']}",
        f"session_id: {review['session_id']}",
        f"action: {review['action']}",
        "---",
        "",
        "# Doctor Execution Review",
        "",
        "This is the fourth-stage execution report. It is only created after the answer stage is approved.",
        "",
        "## Inputs",
        "",
        f"- Answer packet: `{review.get('answer_packet_md_path')}`",
        f"- Answer: `{review.get('answer_md_path')}`",
        f"- Artifacts directory: `{review.get('artifacts_dir')}`",
        "",
        "## Command Runs",
        "",
    ]
    if review.get("commands"):
        for command in review["commands"]:
            lines.extend(
                [
                    f"### {command['run_id']}",
                    "",
                    f"- Command: `{command['command']}`",
                    f"- CWD: `{command['cwd']}`",
                    f"- Return code: `{command.get('returncode')}`",
                    f"- Timed out: `{str(command.get('timed_out')).lower()}`",
                    f"- stdout: `{command['stdout_path']}`",
                    f"- stderr: `{command['stderr_path']}`",
                    f"- result: `{command['result_json_path']}`",
                    "",
                ]
            )
    else:
        lines.append("- No commands have run yet.")
        lines.append("")
    lines.extend(["## External Artifacts", ""])
    if review.get("external_artifacts"):
        for artifact in review["external_artifacts"]:
            lines.append(f"- `{artifact['path']}` ({artifact['size_bytes']} bytes)")
    else:
        lines.append("- No external artifacts recorded yet.")
    lines.extend(
        [
            "",
            "## Review Commands",
            "",
            "Approve or reject the execution output:",
            "",
            "```bash",
            f"agent-context execution-review --out {execution_out_hint(review)} --session-id {review['session_id']} --action approve --reason \"artifacts are acceptable\"",
            f"agent-context execution-review --out {execution_out_hint(review)} --session-id {review['session_id']} --action reject --reason \"artifacts need revision\"",
            "```",
            "",
        ]
    )
    if review.get("last_review_action"):
        lines.extend(
            [
                "## Latest Decision",
                "",
                f"- Action: `{review.get('last_review_action')}`",
                f"- Reason: {review.get('last_review_reason') or ''}",
                f"- Time: `{review.get('last_reviewed_at')}`",
                "",
            ]
        )
    return "\n".join(lines)


def decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def execution_out_hint(review: dict[str, Any]) -> str:
    path = Path(str(review["execution_review_json_path"]))
    return str(path.parents[3]) if len(path.parents) >= 4 else "."
