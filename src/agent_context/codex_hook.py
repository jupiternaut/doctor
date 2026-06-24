from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text
from .resolver import resolve_context


CODEX_PREFLIGHT_VERSION = "0.1"
MODEL_INPUT_VERSION = "0.1"
CODEX_PREFLIGHT_MODES = {"fast", "deep", "arena"}
CORE_PROJECT_CONCEPT_IDS = (
    "project-plm",
    "project-drama",
    "project-codex-plus-plus",
    "project-gugu",
    "project-doctor",
)
PATH_KEYS = (
    "context_md_path",
    "sources_jsonl_path",
    "manifest_json_path",
    "resolution_plan_json_path",
)


def build_codex_preflight(
    out_root: str | Path,
    goal: str,
    source_scope: str = "all",
    limit: int = 12,
    *,
    auto_context: bool = True,
    mode: str = "fast",
    retrieval_goal: str | None = None,
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    normalized_limit = max(1, int(limit))
    normalized_mode = mode if mode in CODEX_PREFLIGHT_MODES else "fast"
    normalized_retrieval_goal = (retrieval_goal or goal).strip() or goal
    metadata: dict[str, Any] = {
        "codex_preflight_version": CODEX_PREFLIGHT_VERSION,
        "auto_context": bool(auto_context),
        "mode": normalized_mode,
        "requested_mode": mode,
        "goal": goal,
        "retrieval_goal": normalized_retrieval_goal,
        "source_scope": source_scope,
        "limit": normalized_limit,
        "out_root": str(root),
    }

    if not auto_context:
        result = {
            **metadata,
            "status": "disabled",
            "paths": {},
            **{key: None for key in PATH_KEYS},
        }
        return finalize_preflight(root, result)

    try:
        resolved = resolve_context(
            root,
            normalized_retrieval_goal,
            limit=normalized_limit,
            source_scope=source_scope,
        )
    except Exception as exc:
        result = {
            **metadata,
            "status": "resolver_failed",
            "error": str(exc),
            "fallback": "continue_without_context",
            "paths": {},
            **{key: None for key in PATH_KEYS},
        }
        return finalize_preflight(root, result)

    paths = {key: resolved.get(key) for key in PATH_KEYS}
    result = {
        **metadata,
        "status": "ok",
        "resolver_version": resolved.get("resolver_version"),
        "route": resolved.get("route"),
        "task_id": resolved.get("task_id"),
        "intent": resolved.get("intent"),
        "selected_sources": resolved.get("selected_sources", []),
        "queries": resolved.get("queries", []),
        "sources_included": resolved.get("sources_included", 0),
        "source_scope": resolved.get("source_scope", source_scope),
        "paths": paths,
        **paths,
    }
    return finalize_preflight(root, result)


def finalize_preflight(root: Path, preflight: dict[str, Any]) -> dict[str, Any]:
    if preflight["status"] == "ok" and preflight.get("context_md_path"):
        preflight["model_input_md_path"] = str(Path(str(preflight["context_md_path"])).parent / "model_input.md")
    else:
        preflight["model_input_md_path"] = None
    preflight["preflight_markdown"] = render_codex_preflight(preflight)
    persist_preflight(root, preflight)
    paths = dict(preflight.get("paths") or {})
    if preflight.get("model_input_md_path"):
        paths["model_input_md_path"] = preflight["model_input_md_path"]
    preflight["paths"] = paths
    return preflight


def render_codex_preflight(preflight: dict[str, Any]) -> str:
    lines = [
        "---",
        f"codex_preflight_version: {preflight['codex_preflight_version']}",
        f"status: {preflight['status']}",
        f"auto_context: {str(preflight['auto_context']).lower()}",
        f"mode: {preflight['mode']}",
        f"requested_mode: {preflight['requested_mode']}",
        f"source_scope: {preflight['source_scope']}",
        f"limit: {preflight['limit']}",
        f"out_root: {preflight['out_root']}",
    ]
    for key in ("resolver_version", "route", "task_id", "intent", "sources_included"):
        if preflight.get(key) is not None:
            lines.append(f"{key}: {preflight[key]}")
    lines.extend(["---", "", "# Codex Preflight", "", "## Goal", "", preflight["goal"], ""])
    if preflight.get("retrieval_goal") and preflight["retrieval_goal"] != preflight["goal"]:
        lines.extend(["## Retrieval Goal", "", preflight["retrieval_goal"], ""])

    status = preflight["status"]
    if status == "ok":
        lines.extend(
            [
                "## Context Pack",
                "",
                f"- Context: `{preflight['context_md_path']}`",
                f"- Sources: `{preflight['sources_jsonl_path']}`",
                f"- Manifest: `{preflight['manifest_json_path']}`",
                f"- Resolution plan: `{preflight['resolution_plan_json_path']}`",
                f"- Model input review: `{preflight['model_input_md_path']}`",
                "",
                "## Use Before Task",
                "",
                "- Read the context pack before making task decisions.",
                "- Review `model_input.md` before sending the context payload to a model.",
                "- Treat resolver output as local evidence, not as a complete transcript or repository scan.",
            ]
        )
        if preflight["mode"] == "deep":
            lines.append("- Deep mode: inspect the resolution plan and top source files before deciding.")
        elif preflight["mode"] == "arena":
            lines.append("- Arena mode: use this preflight as shared context before comparing candidate approaches.")
        else:
            lines.append("- Fast mode: start with the context pack and top listed sources.")
    elif status == "disabled":
        lines.extend(
            [
                "## Context Pack",
                "",
                "- Auto context is disabled; no resolver pack was generated.",
                "",
                "## Use Before Task",
                "",
                "- Continue without preflight context unless the caller enables auto_context.",
            ]
        )
    else:
        lines.extend(
            [
                "## Context Pack",
                "",
                "- Resolver preflight failed; no resolver pack was generated.",
                f"- Error: `{preflight.get('error', '')}`",
                "",
                "## Use Before Task",
                "",
                "- Continue without preflight context or retry after refreshing local indexes.",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def persist_preflight(root: Path, preflight: dict[str, Any]) -> None:
    context_path = preflight.get("context_md_path")
    if context_path:
        preflight_path = Path(str(context_path)).parent / "codex_preflight.md"
    else:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        preflight_path = ensure_dir(root / "packs" / f"codex-preflight-{preflight['status']}-{timestamp}") / "codex_preflight.md"
    if preflight.get("model_input_md_path"):
        write_text(Path(str(preflight["model_input_md_path"])), render_model_input(preflight))
    write_text(preflight_path, preflight["preflight_markdown"])
    preflight["preflight_markdown_path"] = str(preflight_path)


def render_model_input(preflight: dict[str, Any]) -> str:
    root = Path(str(preflight["out_root"]))
    context_path = Path(str(preflight["context_md_path"]))
    try:
        context_body = context_path.read_text(encoding="utf-8")
    except OSError as exc:
        context_body = f"_Context pack could not be read: {type(exc).__name__}: {exc}_\n"
    mode = preflight["mode"] if preflight["mode"] in CODEX_PREFLIGHT_MODES else "fast"
    core_concepts = render_core_project_concepts(root) if mode == "deep" else ""
    context_tokens = estimate_tokens(context_body)
    concept_tokens = estimate_tokens(core_concepts)
    total_tokens = estimate_tokens(preflight["goal"]) + context_tokens + concept_tokens
    target_budget = "10k-20k tokens" if mode == "deep" else "2k-5k tokens"
    mode_summary = (
        "Deep mode includes the hot context pack plus core project concept pages."
        if mode == "deep"
        else "Fast mode includes only the normalized prompt and hot context pack."
    )
    lines = [
        "---",
        f"doctor_model_input_version: {MODEL_INPUT_VERSION}",
        f"codex_preflight_version: {preflight['codex_preflight_version']}",
        f"task_id: {preflight.get('task_id', '')}",
        f"mode: {mode}",
        f"target_context_budget: {target_budget}",
        f"estimated_model_input_tokens: {total_tokens}",
        f"context_pack_tokens: {context_tokens}",
        f"core_project_concept_tokens: {concept_tokens}",
        f"source_scope: {preflight['source_scope']}",
        f"limit: {preflight['limit']}",
        f"context_md_path: {preflight.get('context_md_path')}",
        f"sources_jsonl_path: {preflight.get('sources_jsonl_path')}",
        f"manifest_json_path: {preflight.get('manifest_json_path')}",
        f"resolution_plan_json_path: {preflight.get('resolution_plan_json_path')}",
        "---",
        "",
        "# Doctor Model Input Review",
        "",
        "This is the visible Doctor context payload proposed for the model. It does not include any hidden platform or client system prompts.",
        "",
        "## Confirmed User Prompt",
        "",
        preflight["goal"],
        "",
        "## Doctor Injection Contract",
        "",
        f"- Context mode: `{mode}`.",
        f"- Target context budget: `{target_budget}`.",
        f"- Estimated visible payload: `{total_tokens}` tokens by ceil(chars/3).",
        f"- {mode_summary}",
        "- Use the Doctor context pack below as local evidence for the task.",
        "- Separate local evidence from inference.",
        "- Mention when sources are weak, stale, missing, or only metadata-level.",
        "- Prefer cited local paths from the context pack over unsupported memory.",
        "",
        "## Doctor Context Pack",
        "",
        f"Source file: `{context_path}`",
        "",
        context_body,
    ]
    if core_concepts:
        lines.extend(["", "## Doctor Core Project Concepts", "", core_concepts])
    return "\n".join(lines)


def render_core_project_concepts(root: Path) -> str:
    vault_projects = root / "vault" / "projects"
    blocks: list[str] = []
    for concept_id in CORE_PROJECT_CONCEPT_IDS:
        path = vault_projects / f"{concept_id}.md"
        if not path.exists():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        blocks.extend([f"### {concept_id}", "", f"Source file: `{path}`", "", body.strip(), ""])
    if not blocks:
        return ""
    return "\n".join(blocks).rstrip() + "\n"


def estimate_tokens(text: str) -> int:
    return (len(text) + 2) // 3
