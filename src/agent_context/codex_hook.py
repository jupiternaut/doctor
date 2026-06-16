from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text
from .resolver import resolve_context


CODEX_PREFLIGHT_VERSION = "0.1"
CODEX_PREFLIGHT_MODES = {"fast", "deep", "arena"}
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
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    normalized_limit = max(1, int(limit))
    normalized_mode = mode if mode in CODEX_PREFLIGHT_MODES else "fast"
    metadata: dict[str, Any] = {
        "codex_preflight_version": CODEX_PREFLIGHT_VERSION,
        "auto_context": bool(auto_context),
        "mode": normalized_mode,
        "requested_mode": mode,
        "goal": goal,
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
        result["preflight_markdown"] = render_codex_preflight(result)
        persist_preflight(root, result)
        return result

    try:
        resolved = resolve_context(
            root,
            goal,
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
        result["preflight_markdown"] = render_codex_preflight(result)
        persist_preflight(root, result)
        return result

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
    result["preflight_markdown"] = render_codex_preflight(result)
    persist_preflight(root, result)
    return result


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
                "",
                "## Use Before Task",
                "",
                "- Read the context pack before making task decisions.",
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
    write_text(preflight_path, preflight["preflight_markdown"])
    preflight["preflight_markdown_path"] = str(preflight_path)
