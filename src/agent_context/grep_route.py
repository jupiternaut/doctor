from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GREP_ROUTE_VERSION = "0.1"
DEFAULT_MAX_HITS_PER_TARGET = 40
DEFAULT_MAX_TOTAL_HITS = 120


@dataclass(frozen=True)
class GrepTarget:
    source_id: str
    label: str
    paths: tuple[str, ...]
    weight: float = 1.0


GREP_TARGETS = (
    GrepTarget(
        "downloads_documents",
        "downloads manifests and extracted Markdown",
        (
            "manifests/documents.jsonl",
            "manifests/chunks.jsonl",
            "extracted",
        ),
        1.0,
    ),
    GrepTarget(
        "git_repositories",
        "project manifests and indexed code chunks",
        (
            "manifests/projects.jsonl",
            "manifests/project_documents.jsonl",
            "manifests/project_chunks.jsonl",
            "manifests/symbols.jsonl",
        ),
        1.1,
    ),
    GrepTarget(
        "codebase_memory",
        "codebase-memory provider mappings",
        (
            "manifests/codebase_memory_sources.jsonl",
            "reports/codebase-memory-latest.json",
            "reports/codebase-memory-latest.md",
        ),
        0.9,
    ),
    GrepTarget(
        "codex_sessions",
        "agent session manifests and indexed transcript chunks",
        (
            "manifests/sessions.jsonl",
            "manifests/session_documents.jsonl",
            "manifests/session_chunks.jsonl",
        ),
        1.0,
    ),
    GrepTarget(
        "workflow_docs",
        "workflow provider manifests",
        (
            "manifests/workflows.jsonl",
            "docs",
        ),
        1.0,
    ),
    GrepTarget(
        "media_profile",
        "media and user profile manifests",
        (
            "manifests/douyin_videos.jsonl",
            "manifests/douyin_authors.jsonl",
            "manifests/douyin_assets.jsonl",
            "profiles",
            "extracted/douyin",
        ),
        1.0,
    ),
)

SOURCE_SCOPE_TO_GREP_TARGETS = {
    "downloads": {"downloads_documents"},
    "gitProjects": {"git_repositories", "codebase_memory"},
    "codebaseMemory": {"codebase_memory"},
    "codexSessions": {"codex_sessions"},
    "agentSessions": {"codex_sessions"},
    "workflowDocs": {"workflow_docs"},
    "all": {target.source_id for target in GREP_TARGETS},
}

STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "一个",
    "哪些",
    "如何",
    "怎么",
    "告诉我",
    "本地",
    "所有",
}


def run_grep_route_probe(
    out_root: Path,
    goal: str,
    *,
    terms: list[str] | None = None,
    source_scope: str = "all",
    max_hits_per_target: int = DEFAULT_MAX_HITS_PER_TARGET,
    max_total_hits: int = DEFAULT_MAX_TOTAL_HITS,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    query_terms = grep_terms_for(goal, terms or [])
    allowed_targets = SOURCE_SCOPE_TO_GREP_TARGETS.get(source_scope, SOURCE_SCOPE_TO_GREP_TARGETS["all"])
    available_targets = [
        target
        for target in GREP_TARGETS
        if target.source_id in allowed_targets and existing_target_paths(out_root, target)
    ]
    rg_path = shutil.which("rg")
    hits: list[dict[str, Any]] = []
    for target in available_targets:
        paths = existing_target_paths(out_root, target)
        target_hits = search_target(
            paths,
            query_terms,
            target=target,
            rg_path=rg_path,
            max_hits=max(1, max_hits_per_target),
        )
        hits.extend(target_hits)
        if len(hits) >= max_total_hits:
            hits = hits[:max_total_hits]
            break

    provider_scores = score_provider_hits(hits)
    return {
        "grep_route_version": GREP_ROUTE_VERSION,
        "status": "ok" if query_terms else "no_terms",
        "engine": "ripgrep" if rg_path else "python_fallback",
        "source_scope": source_scope,
        "query_terms": query_terms,
        "targets_considered": [target.source_id for target in available_targets],
        "provider_scores": provider_scores,
        "hits": hits,
    }


def grep_terms_for(goal: str, terms: list[str] | None = None) -> list[str]:
    raw = list(terms or [])
    raw.extend(re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]+", goal.lower()))
    lowered_goal = goal.lower()
    if "推荐" in lowered_goal or "recommend" in lowered_goal:
        raw.extend(["推荐系统", "推荐", "recommendation", "recommender", "ranking", "rank", "feedback", "rerank"])
    if "抖音" in lowered_goal or "douyin" in lowered_goal or "视频" in lowered_goal:
        raw.extend(["抖音", "douyin", "视频", "video", "用户画像", "profile", "author"])
    if "开源" in lowered_goal:
        raw.extend(["开源", "open source", "opensource", "gnu", "linux"])
    if "workflow" in lowered_goal or "上下文" in lowered_goal or "热包" in lowered_goal:
        raw.extend(["workflow", "context", "上下文", "热包", "handoff"])
    result = []
    for term in raw:
        normalized = normalize_term(term)
        if len(normalized) < 2 or normalized in STOP_TERMS:
            continue
        result.append(normalized)
    return list(dict.fromkeys(result))[:24]


def normalize_term(term: str) -> str:
    return re.sub(r"\s+", " ", str(term).strip().lower())


def existing_target_paths(out_root: Path, target: GrepTarget) -> list[Path]:
    paths = []
    for relative in target.paths:
        path = out_root / relative
        if path.exists():
            paths.append(path)
    return paths


def search_target(
    paths: list[Path],
    terms: list[str],
    *,
    target: GrepTarget,
    rg_path: str | None,
    max_hits: int,
) -> list[dict[str, Any]]:
    if not paths or not terms:
        return []
    if rg_path:
        return search_target_with_rg(paths, terms, target=target, rg_path=rg_path, max_hits=max_hits)
    return search_target_with_python(paths, terms, target=target, max_hits=max_hits)


def search_target_with_rg(
    paths: list[Path],
    terms: list[str],
    *,
    target: GrepTarget,
    rg_path: str,
    max_hits: int,
) -> list[dict[str, Any]]:
    cmd = [
        rg_path,
        "--line-number",
        "--with-filename",
        "--color",
        "never",
        "--fixed-strings",
        "--ignore-case",
        "--max-count",
        str(max_hits),
    ]
    for term in terms:
        cmd.extend(["-e", term])
    cmd.extend(str(path) for path in paths)
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode not in {0, 1}:
        return []
    hits = []
    for line in completed.stdout.splitlines():
        hit = parse_rg_line(line, target=target, terms=terms)
        if hit:
            hits.append(hit)
        if len(hits) >= max_hits:
            break
    return hits


def parse_rg_line(line: str, *, target: GrepTarget, terms: list[str]) -> dict[str, Any] | None:
    parts = line.split(":", 2)
    if len(parts) != 3:
        return None
    path, line_number, text = parts
    try:
        parsed_line = int(line_number)
    except ValueError:
        return None
    return {
        "source_id": target.source_id,
        "target": target.label,
        "path": path,
        "line": parsed_line,
        "text": compact_line(text),
        "matched_terms": matched_terms_for(text, path, terms),
    }


def search_target_with_python(
    paths: list[Path],
    terms: list[str],
    *,
    target: GrepTarget,
    max_hits: int,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    lowered_terms = [term.lower() for term in terms]
    for root in paths:
        files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for index, line in enumerate(handle, start=1):
                        lowered = f"{path}\n{line}".lower()
                        if any(term in lowered for term in lowered_terms):
                            hits.append(
                                {
                                    "source_id": target.source_id,
                                    "target": target.label,
                                    "path": str(path),
                                    "line": index,
                                    "text": compact_line(line),
                                    "matched_terms": matched_terms_for(line, str(path), terms),
                                }
                            )
                            if len(hits) >= max_hits:
                                return hits
            except OSError:
                continue
    return hits


def matched_terms_for(text: str, path: str, terms: list[str]) -> list[str]:
    haystack = f"{path}\n{text}".lower()
    return [term for term in terms if term.lower() in haystack][:8]


def score_provider_hits(hits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        grouped.setdefault(str(hit.get("source_id") or "unknown"), []).append(hit)
    scores: dict[str, dict[str, Any]] = {}
    weights = {target.source_id: target.weight for target in GREP_TARGETS}
    for source_id, source_hits in grouped.items():
        unique_files = {str(hit.get("path") or "") for hit in source_hits}
        matched_terms = {
            term
            for hit in source_hits
            for term in (hit.get("matched_terms") or [])
        }
        score = min(1.0, weights.get(source_id, 1.0) * (len(source_hits) * 0.08 + len(unique_files) * 0.12 + len(matched_terms) * 0.05))
        scores[source_id] = {
            "score": round(score, 6),
            "hits": len(source_hits),
            "unique_files": len(unique_files),
            "matched_terms": sorted(matched_terms)[:12],
            "top_hits": source_hits[:5],
        }
    return dict(sorted(scores.items(), key=lambda item: (-item[1]["score"], item[0])))


def compact_line(text: str, *, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
