from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import append_jsonl, ensure_dir, write_text


ANSWER_REVIEW_VERSION = "0.1"
ANSWER_REVIEW_ACTIONS = {"prepare", "record", "approve", "reject"}


def run_answer_review(
    out_root: str | Path,
    *,
    action: str,
    session_id: str,
    answer_text: str = "",
    answer_file: str | Path | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if action not in ANSWER_REVIEW_ACTIONS:
        raise ValueError(f"unknown answer review action: {action}")
    if not session_id:
        raise ValueError("session_id is required")
    root = Path(out_root).expanduser().resolve()
    if action == "prepare":
        return prepare_answer_review(root, session_id=session_id, reason=reason)
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
        "answer_packet_md_path": str(session_dir / "answer_packet.md"),
        "answer_review_json_path": str(session_dir / "answer_review.json"),
        "events_jsonl_path": str(session_dir / "answer_review_events.jsonl"),
        "global_feedback_jsonl_path": str(root / "feedback" / "answer_review_feedback.jsonl"),
        "refined_prompt": context_review.get("refined_prompt", ""),
        "answer_text": "",
    }
    persist_answer_review(root, review, event_action="prepare", event_reason=reason)
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


def persist_answer_review(root: Path, review: dict[str, Any], *, event_action: str, event_reason: str) -> None:
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
