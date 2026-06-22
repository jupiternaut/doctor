from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .codex_hook import build_codex_preflight, finalize_preflight
from .comparison import build_comparison_pack, left_goal_for_comparison
from .io import append_jsonl, ensure_dir, read_jsonl, write_text
from .pack import slugify
from .resolver import resolve_context
from .resume import extract_resume_from_attachments


CONTEXT_REVIEW_VERSION = "0.1"
REVIEW_ACTIONS = {"generate", "regenerate", "approve", "reject"}


def run_context_review(
    out_root: str | Path,
    *,
    action: str,
    refined_prompt_path: str | Path | None = None,
    session_id: str | None = None,
    reason: str = "",
    source_scope: str = "all",
    limit: int = 12,
    mode: str = "fast",
) -> dict[str, Any]:
    if action not in REVIEW_ACTIONS:
        raise ValueError(f"unknown context review action: {action}")
    root = Path(out_root).expanduser().resolve()
    if action in {"generate", "regenerate"}:
        return generate_context_review(
            root,
            action=action,
            refined_prompt_path=refined_prompt_path,
            session_id=session_id,
            reason=reason,
            source_scope=source_scope,
            limit=max(1, int(limit)),
            mode=mode,
        )
    return record_context_review_decision(root, action=action, session_id=session_id, reason=reason)


def generate_context_review(
    root: Path,
    *,
    action: str,
    refined_prompt_path: str | Path | None,
    session_id: str | None,
    reason: str,
    source_scope: str,
    limit: int,
    mode: str,
) -> dict[str, Any]:
    prompt_path = resolve_refined_prompt_path(root, refined_prompt_path=refined_prompt_path, session_id=session_id)
    refined_prompt = extract_refined_prompt(prompt_path)
    retrieval_goal = extract_retrieval_goal(refined_prompt)
    session = session_id or session_id_from_refined_prompt_path(root, prompt_path) or f"session-{slugify(refined_prompt)}"
    session_dir = ensure_dir(root / "runtime" / "sessions" / session)
    if is_runtime_comparison_task(refined_prompt, retrieval_goal):
        preflight = build_comparison_preflight(
            root,
            refined_prompt=refined_prompt,
            retrieval_goal=retrieval_goal,
            prompt_path=prompt_path,
            session_id=session,
            source_scope=source_scope,
            limit=limit,
            mode=mode,
        )
    else:
        preflight = build_codex_preflight(
            root,
            refined_prompt,
            source_scope=source_scope,
            limit=limit,
            mode=mode,
            retrieval_goal=retrieval_goal,
        )
    review = {
        "context_review_version": CONTEXT_REVIEW_VERSION,
        "stage": "resolve_review",
        "status": "pending_review",
        "action": action,
        "created_at": datetime.now().astimezone().isoformat(),
        "session_id": session,
        "reason": reason,
        "refined_prompt_md_path": str(prompt_path),
        "refined_prompt": refined_prompt,
        "retrieval_goal": retrieval_goal,
        "source_scope": source_scope,
        "limit": limit,
        "mode": mode,
        "preflight": summarize_preflight(preflight),
        "context_review_json_path": str(session_dir / "context_review.json"),
        "context_review_md_path": str(session_dir / "context_review.md"),
        "events_jsonl_path": str(session_dir / "context_review_events.jsonl"),
        "global_feedback_jsonl_path": str(root / "feedback" / "context_review_feedback.jsonl"),
    }
    persist_context_review(root, review, event_action=action, event_reason=reason)
    return review


def is_runtime_comparison_task(refined_prompt: str, retrieval_goal: str) -> bool:
    text = f"{retrieval_goal}\n{refined_prompt}"
    lower = text.lower()
    has_compare = any(marker in text or marker in lower for marker in ("比较", "区别", "对比", "比起来", "差异", "compare", " vs "))
    has_resume = "简历" in text or "resume" in lower
    return has_compare and has_resume


def build_comparison_preflight(
    root: Path,
    *,
    refined_prompt: str,
    retrieval_goal: str,
    prompt_path: Path,
    session_id: str,
    source_scope: str,
    limit: int,
    mode: str,
) -> dict[str, Any]:
    session_dir = ensure_dir(root / "runtime" / "sessions" / session_id)
    attachments = load_session_attachments(root, session_id)
    resume = extract_resume_from_attachments(attachments, session_dir) if attachments else None
    left_resolve = resolve_context(
        root,
        left_goal_for_comparison(retrieval_goal),
        limit=max(1, int(limit)),
        source_scope=source_scope,
    )
    left_sources = read_jsonl(Path(str(left_resolve["sources_jsonl_path"])))
    comparison = build_comparison_pack(
        root,
        run_id=session_id,
        user_text=retrieval_goal,
        input_md_path=prompt_path,
        input_markdown="",
        attachments=attachments,
        resume=resume,
        left_resolve_result=left_resolve,
        left_sources=left_sources,
    )
    paths = {
        "context_md_path": comparison["context_md_path"],
        "sources_jsonl_path": comparison["sources_jsonl_path"],
        "manifest_json_path": comparison["manifest_json_path"],
        "resolution_plan_json_path": comparison["comparison_plan_json_path"],
    }
    preflight = {
        "codex_preflight_version": "0.1",
        "auto_context": True,
        "mode": mode if mode in {"fast", "deep", "arena"} else "fast",
        "requested_mode": mode,
        "goal": refined_prompt,
        "retrieval_goal": retrieval_goal,
        "source_scope": source_scope,
        "limit": max(1, int(limit)),
        "out_root": str(root),
        "status": "ok",
        "resolver_version": left_resolve.get("resolver_version"),
        "route": "comparison_slots_v0",
        "task_id": comparison["task_id"],
        "intent": "comparison",
        "selected_sources": left_resolve.get("selected_sources", []),
        "queries": left_resolve.get("queries", []),
        "sources_included": comparison["sources_included"],
        "attachments_included": len(attachments),
        "comparison_plan_json_path": comparison["comparison_plan_json_path"],
        "paths": paths,
        **paths,
    }
    return finalize_preflight(root, preflight)


def load_session_attachments(root: Path, session_id: str) -> list[dict[str, Any]]:
    path = root / "runtime" / "sessions" / session_id / "attachments.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def record_context_review_decision(root: Path, *, action: str, session_id: str | None, reason: str) -> dict[str, Any]:
    if not session_id:
        raise ValueError("session_id is required for approve/reject")
    review_path = root / "runtime" / "sessions" / session_id / "context_review.json"
    if not review_path.exists():
        raise FileNotFoundError(f"context review state not found: {review_path}")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["status"] = "approved" if action == "approve" else "rejected"
    review["last_review_action"] = action
    review["last_review_reason"] = reason
    review["last_reviewed_at"] = datetime.now().astimezone().isoformat()
    persist_context_review(root, review, event_action=action, event_reason=reason)
    return review


def resolve_refined_prompt_path(
    root: Path,
    *,
    refined_prompt_path: str | Path | None,
    session_id: str | None,
) -> Path:
    if refined_prompt_path:
        path = Path(refined_prompt_path).expanduser().resolve()
    elif session_id:
        path = (root / "runtime" / "sessions" / session_id / "refined_prompt.md").resolve()
    else:
        raise ValueError("refined_prompt_path or session_id is required")
    if not path.exists():
        raise FileNotFoundError(f"refined prompt not found: {path}")
    return path


def session_id_from_refined_prompt_path(root: Path, prompt_path: Path) -> str | None:
    try:
        relative = prompt_path.relative_to(root / "runtime" / "sessions")
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def extract_refined_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    marker = "## Refined Prompt"
    if marker not in text:
        return text.strip()
    section = text.split(marker, 1)[1]
    next_heading = section.find("\n## ")
    if next_heading >= 0:
        section = section[:next_heading]
    return section.strip()


def extract_retrieval_goal(refined_prompt: str) -> str:
    for line in refined_prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("任务目标："):
            return strip_retrieval_meta_clauses(stripped.split("：", 1)[1].strip()) or stripped
        if stripped.lower().startswith("task goal:"):
            return strip_retrieval_meta_clauses(stripped.split(":", 1)[1].strip()) or stripped
    return strip_retrieval_meta_clauses(refined_prompt.strip()) or refined_prompt.strip()


def strip_retrieval_meta_clauses(goal: str) -> str:
    parts = re.split(r"[;；。]\s*", goal)
    kept: list[str] = []
    meta_markers = (
        "输入包含",
        "需要先",
        "不访问",
        "等待用户",
        "等待审查",
        "归一化",
        "冷索引",
        "生成上下文",
        "review",
        "index",
        "context",
    )
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(marker.lower() in lower for marker in meta_markers):
            continue
        kept.append(stripped)
    return "；".join(kept).strip()


def summarize_preflight(preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": preflight.get("status"),
        "task_id": preflight.get("task_id"),
        "intent": preflight.get("intent"),
        "sources_included": preflight.get("sources_included"),
        "context_md_path": preflight.get("context_md_path"),
        "sources_jsonl_path": preflight.get("sources_jsonl_path"),
        "manifest_json_path": preflight.get("manifest_json_path"),
        "resolution_plan_json_path": preflight.get("resolution_plan_json_path"),
        "preflight_markdown_path": preflight.get("preflight_markdown_path"),
        "model_input_md_path": preflight.get("model_input_md_path"),
    }


def persist_context_review(root: Path, review: dict[str, Any], *, event_action: str, event_reason: str) -> None:
    review_path = Path(str(review["context_review_json_path"]))
    markdown_path = Path(str(review["context_review_md_path"]))
    write_text(review_path, json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(markdown_path, render_context_review_markdown(review))
    event = review_event(review, action=event_action, reason=event_reason)
    append_jsonl(Path(str(review["events_jsonl_path"])), event)
    if event_action in {"approve", "reject"}:
        append_jsonl(root / "feedback" / "context_review_feedback.jsonl", event)


def review_event(review: dict[str, Any], *, action: str, reason: str) -> dict[str, Any]:
    preflight = review.get("preflight") or {}
    return {
        "context_review_version": CONTEXT_REVIEW_VERSION,
        "timestamp": datetime.now().astimezone().isoformat(),
        "session_id": review.get("session_id"),
        "stage": "resolve_review",
        "action": action,
        "status": review.get("status"),
        "reason": reason,
        "refined_prompt_md_path": review.get("refined_prompt_md_path"),
        "model_input_md_path": preflight.get("model_input_md_path"),
        "context_md_path": preflight.get("context_md_path"),
        "sources_jsonl_path": preflight.get("sources_jsonl_path"),
    }


def render_context_review_markdown(review: dict[str, Any]) -> str:
    preflight = review.get("preflight") or {}
    session_id = review["session_id"]
    lines = [
        "---",
        f"context_review_version: {review['context_review_version']}",
        f"stage: {review['stage']}",
        f"status: {review['status']}",
        f"session_id: {session_id}",
        f"action: {review['action']}",
        f"source_scope: {review['source_scope']}",
        f"limit: {review['limit']}",
        "---",
        "",
        "# Doctor Context Review",
        "",
        "This stage resolves the accepted refined prompt into a reviewable Doctor model input. Review `model_input.md` before sending the context payload to a model.",
        "",
        "## Refined Prompt",
        "",
        review["refined_prompt"],
        "",
        "## Generated Context Payload",
        "",
        f"- Preflight status: `{preflight.get('status')}`",
        f"- Context: `{preflight.get('context_md_path')}`",
        f"- Sources: `{preflight.get('sources_jsonl_path')}`",
        f"- Manifest: `{preflight.get('manifest_json_path')}`",
        f"- Resolution plan: `{preflight.get('resolution_plan_json_path')}`",
        f"- Model input review: `{preflight.get('model_input_md_path')}`",
        "",
        "## Review Commands",
        "",
        "Approve this context payload:",
        "",
        "```bash",
        context_review_command(review, "--action", "approve", "--reason", quote_arg("context matches intent")),
        "```",
        "",
        "Reject this context payload:",
        "",
        "```bash",
        context_review_command(review, "--action", "reject", "--reason", quote_arg("wrong sources")),
        "```",
        "",
        "Regenerate after changing scope or limit:",
        "",
        "```bash",
        context_review_command(
            review,
            "--action",
            "regenerate",
            "--source-scope",
            "all",
            "--limit",
            str(review["limit"]),
            "--reason",
            quote_arg("try broader context"),
        ),
        "```",
        "",
    ]
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


def review_out_hint(review: dict[str, Any]) -> str:
    path = Path(str(review["context_review_json_path"]))
    return str(path.parents[3]) if len(path.parents) >= 4 else "."


def context_review_command(review: dict[str, Any], *args: str) -> str:
    out_root = Path(review_out_hint(review))
    return " ".join(
        [
            doctor_executable(out_root),
            "context-review",
            "--out",
            quote_arg(str(out_root)),
            "--session-id",
            str(review["session_id"]),
            *args,
        ]
    )


def doctor_executable(root: Path) -> str:
    wrapper = root / "doctor"
    if wrapper.exists():
        return quote_arg(str(wrapper))
    return "doctor"


def quote_arg(value: str) -> str:
    escaped = value.replace("'", "'\"'\"'")
    return f"'{escaped}'"
