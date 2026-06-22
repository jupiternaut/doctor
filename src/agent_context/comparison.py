from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .evidence import attach_evidence_records
from .io import ensure_dir, write_jsonl, write_text
from .pack import slugify
from .resume import resume_source_record


COMPARISON_SCHEMA_VERSION = "0.1"
COMPARISON_MARKERS = ("比较", "区别", "对比", "比起来", "差异", "compare", " vs ")


def is_comparison_task(text: str, attachments: list[dict[str, Any]], resume: dict[str, Any] | None) -> bool:
    lower = text.lower()
    has_compare = any(marker in lower or marker in text for marker in COMPARISON_MARKERS)
    has_resume = bool(resume) or "简历" in text or "resume" in lower
    has_image = any(attachment.get("source_type") == "image" for attachment in attachments)
    return has_compare and has_resume and has_image


def left_goal_for_comparison(text: str) -> str:
    if "codex" in text.lower() or "doctor" in text.lower():
        return (
            "查找我的 Codex Doctor agent-context Codex++ 相关项目证据，"
            "关注项目目标、技术栈、架构层级、MCP/Context Resolver/Evidence DB/反馈排序/冷热索引、"
            "以及可以和 AI 应用实习生简历对比的交付能力。"
        )
    return f"查找我的本地项目证据，用于回答比较任务：{text}"


def build_comparison_pack(
    out_root: Path,
    *,
    run_id: str,
    user_text: str,
    input_md_path: Path,
    attachments: list[dict[str, Any]],
    resume: dict[str, Any] | None,
    left_resolve_result: dict[str, Any],
    left_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now().astimezone()
    task_id = f"{slugify(user_text)}-comparison-{now.strftime('%Y%m%d%H%M%S%f')}"
    pack_dir = ensure_dir(out_root / "packs" / task_id)
    context_path = pack_dir / "context.md"
    sources_path = pack_dir / "sources.jsonl"
    manifest_path = pack_dir / "manifest.json"
    plan_path = pack_dir / "comparison_plan.json"

    right_source = resume_source_record(resume) if resume else missing_resume_source(attachments)
    right_source["slot"] = "right_resume"
    ranked_left_sources = prioritize_left_sources(left_sources)
    slotted_left = []
    for source in ranked_left_sources:
        copied = dict(source)
        copied["slot"] = "left_user_projects"
        slotted_left.append(copied)
    sources = attach_evidence_records([right_source, *slotted_left], goal=user_text)

    plan = {
        "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
        "run_id": run_id,
        "task_id": task_id,
        "user_text": user_text,
        "left_slot": {
            "id": "left_user_projects",
            "description": "User's local Codex/Doctor/project evidence.",
            "resolve_context_path": left_resolve_result.get("context_md_path"),
            "sources_jsonl_path": left_resolve_result.get("sources_jsonl_path"),
            "sources": len(ranked_left_sources),
        },
        "right_slot": {
            "id": "right_resume",
            "description": "Resume image OCR/KV evidence.",
            "resume_md_path": resume.get("resume_md_path") if resume else None,
            "resume_json_path": resume.get("resume_json_path") if resume else None,
            "ocr_status": [result.get("status") for result in (resume or {}).get("ocr", [])],
        },
        "routing_rule": "comparison tasks use two explicit evidence slots instead of one mixed top-source list",
        "limitations": comparison_limits(resume),
    }
    manifest = {
        "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
        "task_id": task_id,
        "run_id": run_id,
        "goal": user_text,
        "created_at": now.isoformat(),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "comparison_plan_json_path": str(plan_path),
        "sources_included": len(sources),
    }

    write_jsonl(sources_path, sources)
    write_text(plan_path, json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(context_path, render_comparison_context(user_text, input_md_path, resume, slotted_left, plan))
    return {
        "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
        "task_id": task_id,
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "comparison_plan_json_path": str(plan_path),
        "sources_included": len(sources),
        "left_sources": len(ranked_left_sources),
        "right_sources": 1,
    }


def missing_resume_source(attachments: list[dict[str, Any]]) -> dict[str, Any]:
    attachment = attachments[0] if attachments else {}
    return {
        "type": "resume_ocr",
        "source_id": "resume-ocr:missing",
        "source_group": "lab_inputs",
        "provider": "doctor_resume_ocr",
        "path": attachment.get("path") or "",
        "relative_path": Path(str(attachment.get("path") or "")).name if attachment.get("path") else None,
        "title": "Resume OCR Evidence Missing",
        "summary": "Resume image was attached, but OCR did not produce a resume record.",
        "snippet": "OCR missing; manual image review required.",
        "source_type": "document",
        "score": 0.0,
    }


def comparison_limits(resume: dict[str, Any] | None) -> list[str]:
    limits = []
    if not resume:
        limits.append("No resume OCR record is available.")
    elif any(result.get("status") != "ok" for result in resume.get("ocr") or []):
        limits.append("At least one OCR attempt failed or returned partial output.")
    if resume and resume.get("limits"):
        limits.extend(resume["limits"])
    return limits


def prioritize_left_sources(left_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(left_sources, key=left_source_rank_key)


def left_source_rank_key(source: dict[str, Any]) -> tuple[float, float, str]:
    path = str(source.get("path") or "").lower()
    preferred = 0.0
    if "agent-context-system" in path or "/doctor" in path:
        preferred = 3.0
    elif "codexplusplus" in path or "codex-plus" in path:
        preferred = 2.5
    elif "recommendation-system" in path:
        preferred = 2.0
    elif "codex" in path:
        preferred = 1.0
    try:
        score = float(source.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return (-preferred, -score, str(source.get("path") or ""))


def render_comparison_context(
    user_text: str,
    input_md_path: Path,
    resume: dict[str, Any] | None,
    left_sources: list[dict[str, Any]],
    plan: dict[str, Any],
) -> str:
    lines = [
        input_md_path.read_text(encoding="utf-8"),
        "---",
        "",
        "# Comparison Task",
        "",
        user_text,
        "",
        "## Evidence Slots",
        "",
        "- `left_user_projects`: user's local Codex/Doctor/project evidence.",
        "- `right_resume`: resume image OCR/KV evidence.",
        "",
        "## Right Slot: Resume Evidence",
        "",
    ]
    if resume:
        lines.append(resume.get("markdown") or "")
    else:
        lines.append("- No resume OCR text is available. Use the attached image for manual review.")
    lines.extend(["", "## Left Slot: User Project Evidence", ""])
    if left_sources:
        for index, source in enumerate(left_sources[:8], start=1):
            lines.append(
                f"{index}. `{source.get('path')}` ({source.get('source_group')}, score={source.get('score')})"
            )
            if source.get("snippet"):
                lines.append(f"   - {source.get('snippet')}")
    else:
        lines.append("- No left-slot project evidence was retrieved.")
    lines.extend(
        [
            "",
            "## Answer Contract",
            "",
            "- Compare the two slots, not a single mixed retrieval list.",
            "- Separate resume claims from local project evidence.",
            "- State when OCR is weak or missing.",
            "- Produce a concise table of capability level, project depth, resume packaging, and next positioning step.",
            "",
            "## Limitations",
            "",
        ]
    )
    limits = plan.get("limitations") or []
    lines.extend(f"- {limit}" for limit in limits) if limits else lines.append("- No extra limitations recorded.")
    lines.append("")
    return "\n".join(lines)
