from __future__ import annotations

import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import append_jsonl, ensure_dir, write_text


ANSWER_REVIEW_VERSION = "0.1"
ANSWER_REVIEW_ACTIONS = {"prepare", "run", "record", "approve", "reject"}


def run_answer_review(
    out_root: str | Path,
    *,
    action: str,
    session_id: str,
    answer_text: str = "",
    answer_file: str | Path | None = None,
    command: str = "",
    cwd: str | Path | None = None,
    timeout_seconds: int = 120,
    reason: str = "",
) -> dict[str, Any]:
    if action not in ANSWER_REVIEW_ACTIONS:
        raise ValueError(f"unknown answer review action: {action}")
    if not session_id:
        raise ValueError("session_id is required")
    root = Path(out_root).expanduser().resolve()
    if action == "prepare":
        return prepare_answer_review(root, session_id=session_id, reason=reason)
    if action == "run":
        return run_answer_command(
            root,
            session_id=session_id,
            command=command,
            cwd=cwd,
            timeout_seconds=max(1, int(timeout_seconds)),
            reason=reason,
        )
    if action == "record":
        return record_answer_output(root, session_id=session_id, answer_text=answer_text, answer_file=answer_file, reason=reason)
    return record_answer_decision(root, action=action, session_id=session_id, reason=reason)


def prepare_answer_review(root: Path, *, session_id: str, reason: str) -> dict[str, Any]:
    context_review = load_context_review(root, session_id)
    if context_review.get("status") != "approved":
        raise ValueError("context_review must be approved before preparing an answer packet")
    preflight = context_review.get("preflight") or {}
    model_input = preflight.get("model_input_md_path")
    if not model_input:
        raise ValueError("approved context_review does not reference model_input.md")
    handoff_path = root / "runtime" / "sessions" / session_id / "agent_handoff.md"
    if not handoff_path.exists():
        raise ValueError("agent_handoff.md must be exported before preparing an answer packet")
    session_dir = ensure_dir(root / "runtime" / "sessions" / session_id)
    review = {
        "answer_review_version": ANSWER_REVIEW_VERSION,
        "stage": "answer_review",
        "status": "awaiting_answer",
        "action": "prepare",
        "created_at": datetime.now().astimezone().isoformat(),
        "session_id": session_id,
        "reason": reason,
        "context_review_json_path": str(session_dir / "context_review.json"),
        "model_input_md_path": model_input,
        "context_md_path": preflight.get("context_md_path"),
        "sources_jsonl_path": preflight.get("sources_jsonl_path"),
        "answer_md_path": str(session_dir / "answer.md"),
        "answer_runs_dir": str(session_dir / "answer_runs"),
        "answer_packet_md_path": str(session_dir / "answer_packet.md"),
        "answer_review_json_path": str(session_dir / "answer_review.json"),
        "events_jsonl_path": str(session_dir / "answer_review_events.jsonl"),
        "global_feedback_jsonl_path": str(root / "feedback" / "answer_review_feedback.jsonl"),
        "refined_prompt": context_review.get("refined_prompt", ""),
        "answer_text": "",
        "answer_runs": [],
    }
    persist_answer_review(root, review, event_action="prepare", event_reason=reason)
    return review


def run_answer_command(
    root: Path,
    *,
    session_id: str,
    command: str,
    cwd: str | Path | None,
    timeout_seconds: int,
    reason: str,
) -> dict[str, Any]:
    if not command.strip():
        raise ValueError("command is required for answer run")
    review = ensure_answer_prepared(root, session_id)
    if review.get("status") == "approved":
        raise ValueError("answer_review is already approved; start a new session before rerunning")
    run_id = datetime.now().strftime("answer-run-%Y%m%d%H%M%S%f")
    runs_dir = ensure_dir(Path(str(review.get("answer_runs_dir") or Path(str(review["answer_review_json_path"])).parent / "answer_runs")))
    cwd_path = Path(cwd).expanduser().resolve() if cwd else root
    if not cwd_path.exists() or not cwd_path.is_dir():
        raise FileNotFoundError(f"answer cwd not found: {cwd_path}")
    argv = shlex.split(command)
    if not argv:
        raise ValueError("command parsed to no argv")

    packet_path = Path(str(review["answer_packet_md_path"]))
    packet_text = packet_path.read_text(encoding="utf-8")
    stdin_path = runs_dir / f"{run_id}.stdin.md"
    stdout_path = runs_dir / f"{run_id}.stdout.txt"
    stderr_path = runs_dir / f"{run_id}.stderr.txt"
    result_path = runs_dir / f"{run_id}.json"
    write_text(stdin_path, packet_text)
    started_at = datetime.now().astimezone()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd_path),
            input=packet_text,
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
        stderr = decode_timeout_output(exc.stderr) or f"answer command timed out after {timeout_seconds} seconds"
    finished_at = datetime.now().astimezone()

    write_text(stdout_path, stdout)
    write_text(stderr_path, stderr)
    answer_run = {
        "run_id": run_id,
        "command": command,
        "argv": argv,
        "cwd": str(cwd_path),
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "returncode": returncode,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "stdin_path": str(stdin_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "result_json_path": str(result_path),
        "reason": reason,
    }
    write_text(result_path, json.dumps(answer_run, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    review.setdefault("answer_runs", []).append(answer_run)
    review["action"] = "run"
    review["last_answer_run_id"] = run_id
    review["last_answer_returncode"] = returncode
    review["last_answer_timed_out"] = timed_out
    if returncode == 0 and not timed_out and stdout.strip():
        review["status"] = "pending_review"
        review["answer_text"] = stdout.strip()
        review["answer_source_path"] = str(stdout_path)
        review["last_recorded_at"] = finished_at.isoformat()
        review["last_record_reason"] = reason
        write_text(Path(str(review["answer_md_path"])), render_answer_markdown(review))
    else:
        review["status"] = "answer_failed"
    persist_answer_review(root, review, event_action="run", event_reason=reason)
    return review


def record_answer_output(
    root: Path,
    *,
    session_id: str,
    answer_text: str,
    answer_file: str | Path | None,
    reason: str,
) -> dict[str, Any]:
    review = load_answer_review(root, session_id)
    text = answer_text
    source_path = None
    if answer_file:
        source_path = str(Path(answer_file).expanduser().resolve())
        text = Path(source_path).read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("answer_text or answer_file with non-empty content is required")
    review["status"] = "pending_review"
    review["action"] = "record"
    review["answer_text"] = text.strip()
    review["answer_source_path"] = source_path
    review["last_recorded_at"] = datetime.now().astimezone().isoformat()
    review["last_record_reason"] = reason
    write_text(Path(str(review["answer_md_path"])), render_answer_markdown(review))
    persist_answer_review(root, review, event_action="record", event_reason=reason)
    return review


def record_answer_decision(root: Path, *, action: str, session_id: str, reason: str) -> dict[str, Any]:
    review = load_answer_review(root, session_id)
    if not review.get("answer_text"):
        raise ValueError("answer output must be recorded before approve/reject")
    review["status"] = "approved" if action == "approve" else "rejected"
    review["action"] = action
    review["last_review_action"] = action
    review["last_review_reason"] = reason
    review["last_reviewed_at"] = datetime.now().astimezone().isoformat()
    persist_answer_review(root, review, event_action=action, event_reason=reason)
    return review


def load_context_review(root: Path, session_id: str) -> dict[str, Any]:
    path = root / "runtime" / "sessions" / session_id / "context_review.json"
    if not path.exists():
        raise FileNotFoundError(f"context_review.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_answer_review(root: Path, session_id: str) -> dict[str, Any]:
    path = root / "runtime" / "sessions" / session_id / "answer_review.json"
    if not path.exists():
        raise FileNotFoundError(f"answer_review.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_answer_prepared(root: Path, session_id: str) -> dict[str, Any]:
    path = root / "runtime" / "sessions" / session_id / "answer_review.json"
    if path.exists():
        review = json.loads(path.read_text(encoding="utf-8"))
        review.setdefault("answer_runs_dir", str(path.parent / "answer_runs"))
        review.setdefault("answer_runs", [])
        return review
    return prepare_answer_review(root, session_id=session_id, reason="auto-prepare before answer run")


def persist_answer_review(root: Path, review: dict[str, Any], *, event_action: str, event_reason: str) -> None:
    review.setdefault("answer_runs", [])
    review.setdefault("answer_runs_dir", str(Path(str(review["answer_review_json_path"])).parent / "answer_runs"))
    write_text(Path(str(review["answer_review_json_path"])), json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(Path(str(review["answer_packet_md_path"])), render_answer_packet_markdown(review))
    event = answer_review_event(review, action=event_action, reason=event_reason)
    append_jsonl(Path(str(review["events_jsonl_path"])), event)
    if event_action in {"approve", "reject"}:
        append_jsonl(root / "feedback" / "answer_review_feedback.jsonl", event)


def answer_review_event(review: dict[str, Any], *, action: str, reason: str) -> dict[str, Any]:
    return {
        "answer_review_version": ANSWER_REVIEW_VERSION,
        "timestamp": datetime.now().astimezone().isoformat(),
        "session_id": review.get("session_id"),
        "stage": "answer_review",
        "action": action,
        "status": review.get("status"),
        "reason": reason,
        "model_input_md_path": review.get("model_input_md_path"),
        "answer_packet_md_path": review.get("answer_packet_md_path"),
        "answer_md_path": review.get("answer_md_path"),
        "last_answer_run_id": review.get("last_answer_run_id"),
        "last_answer_returncode": review.get("last_answer_returncode"),
    }


def render_answer_packet_markdown(review: dict[str, Any]) -> str:
    lines = [
        "---",
        f"answer_review_version: {review['answer_review_version']}",
        f"stage: {review['stage']}",
        f"status: {review['status']}",
        f"session_id: {review['session_id']}",
        f"action: {review['action']}",
        "---",
        "",
        "# Doctor Answer Review",
        "",
        "This packet is the third-stage handoff. It may be used by Codex++, Warp, or Doctor only after the context payload has been approved.",
        "",
        "## Approved Context Payload",
        "",
        f"- Model input: `{review['model_input_md_path']}`",
        f"- Context: `{review.get('context_md_path')}`",
        f"- Sources: `{review.get('sources_jsonl_path')}`",
        "",
        "## Refined Prompt",
        "",
        review.get("refined_prompt") or "",
        "",
        "## Answer Instructions",
        "",
        "- Use the approved `model_input.md` as the evidence payload.",
        "- Keep local evidence, inference, limitations, and next steps separate.",
        "- Do not claim sources that are not present in the approved context payload.",
        "- After a model or agent produces an answer, record it with `agent-context answer-review --action record`.",
        "- To let a local agent command produce an answer, use `agent-context answer-review --action run --command ...`; the packet is passed on stdin.",
        "",
        "## Recorded Answer",
        "",
    ]
    if review.get("answer_text"):
        lines.append(review["answer_text"])
    else:
        lines.append("_No answer has been recorded yet._")
    lines.extend(
        [
            "",
            "## Review Commands",
            "",
            "Record an answer:",
            "",
            "```bash",
            f"agent-context answer-review --out {answer_out_hint(review)} --session-id {review['session_id']} --action record --answer-file /path/to/answer.md",
            "```",
            "",
            "Run a local answer command:",
            "",
            "```bash",
            f"agent-context answer-review --out {answer_out_hint(review)} --session-id {review['session_id']} --action run --command \"<agent command>\"",
            "```",
            "",
            "Approve or reject the recorded answer:",
            "",
            "```bash",
            f"agent-context answer-review --out {answer_out_hint(review)} --session-id {review['session_id']} --action approve --reason \"answer matches intent\"",
            f"agent-context answer-review --out {answer_out_hint(review)} --session-id {review['session_id']} --action reject --reason \"answer needs revision\"",
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
    if review.get("answer_runs"):
        lines.extend(["## Answer Command Runs", ""])
        for run in review["answer_runs"]:
            lines.extend(
                [
                    f"### {run['run_id']}",
                    "",
                    f"- Command: `{run['command']}`",
                    f"- CWD: `{run['cwd']}`",
                    f"- Return code: `{run.get('returncode')}`",
                    f"- Timed out: `{str(run.get('timed_out')).lower()}`",
                    f"- stdin: `{run['stdin_path']}`",
                    f"- stdout: `{run['stdout_path']}`",
                    f"- stderr: `{run['stderr_path']}`",
                    f"- result: `{run['result_json_path']}`",
                    "",
                ]
            )
    return "\n".join(lines)


def render_answer_markdown(review: dict[str, Any]) -> str:
    return "\n".join(
        [
            "---",
            f"answer_review_version: {review['answer_review_version']}",
            f"session_id: {review['session_id']}",
            f"status: {review['status']}",
            "---",
            "",
            "# Recorded Answer",
            "",
            review.get("answer_text", ""),
            "",
        ]
    )


def answer_out_hint(review: dict[str, Any]) -> str:
    path = Path(str(review["answer_review_json_path"]))
    return str(path.parents[3]) if len(path.parents) >= 4 else "."


def decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
