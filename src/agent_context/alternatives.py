from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .feedback_model import query_family_for_text, write_feedback_model
from .io import ensure_dir
from .resolver import DEFAULT_RESOLVE_LIMIT, resolve_context


ALTERNATIVE_FEEDBACK_VERSION = "0.1"
ALTERNATIVE_FEEDBACK_FILENAME = "alternative_feedback.jsonl"


def resolve_alternative_context(
    out_root: Path,
    *,
    goal: str,
    rejected_sources: list[str],
    reason: str = "",
    source_scope: str = "all",
    limit: int = DEFAULT_RESOLVE_LIMIT,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    normalized_rejections = normalize_rejected_sources(rejected_sources)
    feedback_result = record_alternative_feedback(
        out_root,
        goal=goal,
        rejected_sources=normalized_rejections,
        reason=reason,
    )
    resolved = resolve_context(
        out_root,
        goal,
        limit=max(1, int(limit)),
        source_scope=source_scope,
        avoid_sources=normalized_rejections,
    )
    return {
        "alternative_feedback_version": ALTERNATIVE_FEEDBACK_VERSION,
        "status": "ok",
        "goal": goal,
        "source_scope": source_scope,
        "rejected_sources": normalized_rejections,
        "reason": reason,
        **feedback_result,
        **resolved,
    }


def record_alternative_feedback(
    out_root: Path,
    *,
    goal: str,
    rejected_sources: list[str],
    reason: str = "",
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    normalized_rejections = normalize_rejected_sources(rejected_sources)
    if not normalized_rejections:
        raise ValueError("at least one rejected source is required")
    feedback_path = ensure_dir(out_root / "feedback") / ALTERNATIVE_FEEDBACK_FILENAME
    record = {
        "alternative_feedback_version": ALTERNATIVE_FEEDBACK_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "goal": goal,
        "query_family": query_family_for_text(goal),
        "rejected_sources": normalized_rejections,
        "rating": "negative",
        "reason": reason,
    }
    with feedback_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    model = write_feedback_model(out_root)
    return {
        "feedback_path": str(feedback_path),
        "feedback_model_path": model["feedback_model_path"],
        "feedback_model_version": model["feedback_model_version"],
        "feedback_record": record,
    }


def normalize_rejected_sources(rejected_sources: list[str]) -> list[str]:
    return list(dict.fromkeys(source.strip() for source in rejected_sources if source and source.strip()))
