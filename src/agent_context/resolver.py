from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .access_policy import filter_records_for_access
from .codebase_memory import codebase_memory_status, search_codebase_memory
from .cold_index import search_cold_index
from .evidence import attach_evidence_records
from .feedback_model import feedback_boost_parts, load_feedback_model, query_family_for_text
from .grep_route import run_grep_route_probe
from .io import ensure_dir, write_jsonl, write_text
from .pack import slugify, snippet
from .project_index import project_index_path_for, search_project_index
from .providers import (
    ensure_provider_manifests,
    load_project_records,
    load_session_records,
    load_workflow_records,
)
from .retrieval_backends import FASTEMBED_BACKEND_ID, default_retrieval_config
from .route_selector import load_route_selector_model, route_selector_boost_parts
from .semantic_index import search_semantic_index, semantic_index_path_for
from .session_index import search_session_index, session_index_path_for


RESOLVER_VERSION = "0.5"
RESOLVER_ROUTE = "rule_based_v0"
DEFAULT_RESOLVE_LIMIT = 12
GENERIC_SESSION_TERMS = {"codex", "claude", "cursor", "会话", "历史", "之前", "查一下", "怎么", "如何"}
PROJECT_DIVERSITY_CAP = 2
SOURCE_SCOPE_TO_SOURCE_IDS = {
    "downloads": ("downloads_documents",),
    "gitProjects": ("git_repositories", "codebase_memory"),
    "codebaseMemory": ("codebase_memory",),
    "codexSessions": ("codex_sessions",),
    "agentSessions": ("codex_sessions",),
    "workflowDocs": ("workflow_docs",),
    "all": ("downloads_documents", "workflow_docs", "git_repositories", "codebase_memory", "codex_sessions"),
}


def resolve_context(
    out_root: Path,
    goal: str,
    limit: int = DEFAULT_RESOLVE_LIMIT,
    source_scope: str = "all",
    avoid_sources: list[str] | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    limit = max(1, limit)
    plan = build_resolution_plan(out_root=out_root, goal=goal, limit=limit, source_scope=source_scope)
    candidates = retrieve_candidates_for_plan(out_root, plan)
    candidates = filter_avoided_candidates(candidates, avoid_sources or [], plan)
    feedback_model = load_feedback_model(out_root)
    route_selector_model = load_route_selector_model(out_root)
    sources = fuse_candidates(
        candidates,
        limit,
        feedback_model=feedback_model,
        route_selector_model=route_selector_model,
        query_family=plan.get("query_family"),
    )
    sources = attach_evidence_records(sources, goal=goal)
    plan["retrieval_stats"] = retrieval_stats(candidates, sources)
    plan["feedback_model"] = feedback_model
    plan["route_selector_model"] = route_selector_model

    now = datetime.now().astimezone()
    created_at = now.isoformat()
    task_id = f"{slugify(goal)}-resolve-{now.strftime('%Y%m%d%H%M%S%f')}"
    pack_dir = ensure_dir(out_root / "packs" / task_id)
    context_path = pack_dir / "context.md"
    sources_path = pack_dir / "sources.jsonl"
    manifest_path = pack_dir / "manifest.json"
    resolution_plan_path = pack_dir / "resolution_plan.json"

    manifest = {
        "resolver_version": RESOLVER_VERSION,
        "route": RESOLVER_ROUTE,
        "task_id": task_id,
        "goal": goal,
        "created_at": created_at,
        "intent": plan["intent"],
        "source_scope": plan["source_scope"],
        "selected_sources": plan["selected_sources"],
        "queries": plan["queries"],
        "retrieval_config": plan["retrieval_config"],
        "avoid_sources": plan.get("avoid_sources", []),
        "sources_included": len(sources),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "resolution_plan_json_path": str(resolution_plan_path),
    }

    write_jsonl(sources_path, sources)
    write_text(resolution_plan_path, json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(context_path, render_context(goal, created_at, plan, sources))

    return {
        "resolver_version": RESOLVER_VERSION,
        "route": RESOLVER_ROUTE,
        "task_id": task_id,
        "goal": goal,
        "intent": plan["intent"],
        "source_scope": plan["source_scope"],
        "selected_sources": plan["selected_sources"],
        "queries": plan["queries"],
        "avoid_sources": plan.get("avoid_sources", []),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "resolution_plan_json_path": str(resolution_plan_path),
        "sources_included": len(sources),
    }


def build_resolution_plan(
    out_root: Path,
    goal: str,
    limit: int = DEFAULT_RESOLVE_LIMIT,
    source_scope: str = "all",
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    limit = max(1, limit)
    source_scope = normalize_source_scope(source_scope)
    terms = terms_for(goal)
    intent = classify_goal(goal, terms)
    entities = entities_for(goal, terms)
    source_candidates = source_registry(out_root)
    grep_route_probe = run_grep_route_probe(out_root, goal, terms=terms, source_scope=source_scope)
    selected_sources, source_reasons = select_sources(intent, goal, source_candidates, grep_route_probe=grep_route_probe)
    selected_sources, source_reasons = apply_source_scope(
        selected_sources,
        source_reasons,
        source_candidates,
        source_scope,
    )
    queries = plan_queries(goal, intent, entities, terms)
    retrieval_config = default_retrieval_config()

    return {
        "resolver_version": RESOLVER_VERSION,
        "route": RESOLVER_ROUTE,
        "routing_rules": [
            "Use deterministic keyword and source-availability rules.",
            "Prefer local indexed sources before metadata-only sources.",
            "Use multiple queries and fuse results instead of writing intermediate query packs.",
            "Use ripgrep as a deterministic L0/L1 route probe before semantic retrieval.",
            "Apply retrieval-eval route selector priors when labeled backend comparisons exist.",
        ],
        "goal": goal,
        "query_family": query_family_for_text(goal),
        "source_scope": source_scope,
        "intent": intent,
        "entities": entities,
        "keywords": terms,
        "selected_sources": selected_sources,
        "source_reasons": source_reasons,
        "source_candidates": source_candidates,
        "grep_route_probe": grep_route_probe,
        "queries": queries,
        "retrieval_config": {
            "embedding_backend": retrieval_config.embedding_backend,
            "ann_backend": retrieval_config.ann_backend,
            "rerank_backend": retrieval_config.rerank_backend,
            "semantic_index_path": str(semantic_index_path_for(out_root)),
        },
        "constraints": {
            "max_context_sources": limit,
            "prefer_recent": intent in {"project_code", "agent_history", "workflow_handoff", "mixed"},
            "prefer_project_files": intent in {"project_code", "workflow_handoff", "mixed"},
            "local_only": True,
        },
        "filters": {
            "prefer_paths": ["docs", "README", "PROJECT_TASK_README", "src", "skills", "workflow"],
            "prefer_extensions": [".md", ".markdown", ".py", ".ts", ".tsx", ".json", ".skill"],
        },
        "refresh_plan": {
            source_id: refresh_action_for(source_id, source_candidates)
            for source_id in selected_sources
        },
        "retrieval_stats": {},
        "semantic_retrieval_errors": [],
        "semantic_retrieval_skips": [],
        "semantic_retrieval_modes": [],
        "semantic_ann_fallbacks": [],
        "semantic_ann_cache_statuses": [],
        "codebase_memory_errors": [],
    }


def classify_goal(goal: str, terms: list[str]) -> str:
    lower = goal.lower()
    project_markers = (
        "项目",
        "代码",
        "project",
        "code",
        "repo",
        "repository",
        "github",
        "build",
        "implement",
        "implementation",
        "architecture",
        "构建",
        "实现",
        "架构",
        "系统",
    )
    document_markers = ("downloads", "下载", "pdf", "docx", "文档", "资料", "文章", "报告")
    history_markers = ("会话", "历史", "之前", "聊过", "codex", "claude", "cursor")
    workflow_markers = ("workflow", "handoff", "readme", "mcp", "agent", "上下文", "热包", "冷索引")

    hits = {
        "project_code": marker_hits(lower, terms, project_markers),
        "document_research": marker_hits(lower, terms, document_markers),
        "agent_history": marker_hits(lower, terms, history_markers),
        "workflow_handoff": marker_hits(lower, terms, workflow_markers),
    }
    positive = [intent for intent, count in hits.items() if count > 0]
    if len(positive) > 1:
        return "mixed"
    if positive:
        return positive[0]
    return "document_research"


def marker_hits(lower_goal: str, terms: list[str], markers: tuple[str, ...]) -> int:
    haystack = " ".join([lower_goal, *terms])
    return sum(1 for marker in markers if marker.lower() in haystack)


def terms_for(goal: str) -> list[str]:
    raw_terms = re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]+", goal.lower())
    stop_terms = {"the", "and", "for", "with", "from", "this", "that", "哪些", "如何", "怎么", "一个"}
    terms = []
    for term in raw_terms:
        if len(term) >= 2 and term not in stop_terms:
            terms.append(term)
    return list(dict.fromkeys(terms)) or [goal.lower()]


def entities_for(goal: str, terms: list[str]) -> list[str]:
    entity_markers = []
    for marker in ("个人推荐系统", "推荐系统", "个人助手", "开源往事", "context resolver", "context router"):
        if marker.lower() in goal.lower():
            entity_markers.append(marker)
    longer_terms = [term for term in terms if len(term) >= 4]
    return list(dict.fromkeys(entity_markers + longer_terms[:4]))


def source_registry(out_root: Path) -> list[dict[str, Any]]:
    provider_paths = ensure_provider_manifests(out_root)
    manifests = out_root / "manifests"
    db_path = out_root / "indexes" / "context.sqlite"
    project_db_path = project_index_path_for(out_root)
    session_db_path = session_index_path_for(out_root)
    codebase_memory = codebase_memory_status(out_root)
    workflows = filter_records_for_access(
        out_root,
        load_workflow_records(out_root),
        audit_action="resolver_filter_workflow_providers",
    )
    projects = filter_records_for_access(
        out_root,
        load_project_records(out_root),
        audit_action="resolver_filter_project_providers",
    )
    sessions = filter_records_for_access(
        out_root,
        load_session_records(out_root),
        audit_action="resolver_filter_session_providers",
    )

    return [
        {
            "source_id": "downloads_documents",
            "kind": "documents",
            "status": "indexed" if db_path.exists() else "manifest_only" if manifests.exists() else "missing",
            "scope": str(out_root),
            "index_path": str(db_path),
            "strengths": ["downloaded references", "documents", "research notes", "agent assets"],
            "weaknesses": ["not project-wide unless indexed manifests include those paths"],
        },
        {
            "source_id": "workflow_docs",
            "kind": "workflow_docs",
            "status": "available" if workflows else "missing",
            "scope": str(provider_paths.workflows_jsonl),
            "paths": [record["path"] for record in workflows[:20]],
            "records": len(workflows),
            "strengths": ["architecture", "handoff", "implementation plan"],
            "weaknesses": ["Markdown workflow cards, not arbitrary binary docs"],
        },
        {
            "source_id": "git_repositories",
            "kind": "project_provider",
            "status": "indexed" if project_db_path.exists() else "available" if projects else "missing",
            "scope": str(provider_paths.projects_jsonl),
            "index_path": str(project_db_path),
            "paths": [record["path"] for record in projects[:20]],
            "records": len(projects),
            "strengths": ["project names", "README/docs", "source files", "symbols", "local repository hints"],
            "weaknesses": ["v0.6 indexes selected text/source files, not binary assets or generated dependency folders"],
        },
        {
            "source_id": "codex_sessions",
            "kind": "session_provider",
            "status": "indexed" if session_db_path.exists() else "available" if sessions else "missing",
            "scope": str(provider_paths.sessions_jsonl),
            "index_path": str(session_db_path),
            "paths": [record["path"] for record in sessions[:20]],
            "records": len(sessions),
            "strengths": ["Codex/Claude history summaries", "session transcript chunks", "cwd hints", "recent user goals"],
            "weaknesses": ["session index is a cleaned transcript preview; it omits tool calls and environment blocks"],
        },
        {
            "source_id": "codebase_memory",
            "kind": "external_code_graph_provider",
            "status": codebase_memory["status"],
            "scope": codebase_memory["pseudo_repo_path"],
            "index_path": codebase_memory["report_json_path"],
            "records": len(codebase_memory.get("projects") or []),
            "paths": [project.get("root_path") for project in (codebase_memory.get("projects") or [])[:20]],
            "strengths": ["external code graph", "search_code over indexed repositories", "Doctor extracted Markdown pseudo repo"],
            "weaknesses": ["requires optional codebase-memory-mcp binary and an explicit provider index refresh"],
        },
    ]

def normalize_source_scope(source_scope: str) -> str:
    return source_scope if source_scope in SOURCE_SCOPE_TO_SOURCE_IDS else "all"


def apply_source_scope(
    selected_sources: list[str],
    source_reasons: dict[str, str],
    source_candidates: list[dict[str, Any]],
    source_scope: str,
) -> tuple[list[str], dict[str, str]]:
    if source_scope == "all":
        return selected_sources, source_reasons

    allowed = set(SOURCE_SCOPE_TO_SOURCE_IDS[source_scope])
    available = {
        candidate["source_id"]
        for candidate in source_candidates
        if source_is_available(candidate) and candidate["source_id"] in allowed
    }
    filtered = [source_id for source_id in selected_sources if source_id in available]
    if not filtered:
        filtered = [source_id for source_id in SOURCE_SCOPE_TO_SOURCE_IDS[source_scope] if source_id in available]
    filtered_reasons = {
        source_id: source_reasons.get(source_id, f"selected by explicit source scope `{source_scope}`")
        for source_id in filtered
    }
    return filtered, filtered_reasons


def select_sources(
    intent: str,
    goal: str,
    candidates: list[dict[str, Any]],
    grep_route_probe: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, str]]:
    available = {candidate["source_id"]: candidate for candidate in candidates if source_is_available(candidate)}
    selected: list[str] = []
    reasons: dict[str, str] = {}

    def add(source_id: str, reason: str) -> None:
        if source_id in available and source_id not in selected:
            selected.append(source_id)
            reasons[source_id] = reason

    for source_id, stats in grep_provider_scores(grep_route_probe).items():
        if float(stats.get("score") or 0.0) < 0.2:
            continue
        add(
            source_id,
            (
                "grep route probe found deterministic local matches"
                f"; hits={stats.get('hits', 0)}; files={stats.get('unique_files', 0)}"
                f"; terms={', '.join(stats.get('matched_terms', [])[:5])}"
            ),
        )

    if intent in {"project_code", "mixed"}:
        add("workflow_docs", "goal looks like project or architecture work; local workflow docs explain current state")
        add("codebase_memory", "goal mentions projects/code; external code graph search can surface implementation-level evidence")
        add("git_repositories", "goal mentions projects/code; repository metadata can orient the search")
        add("downloads_documents", "project goals still benefit from the existing cold index for related research and handoff evidence")
    if intent in {"agent_history", "mixed"}:
        add("codex_sessions", "goal mentions agent/session history; session metadata may identify relevant prior work")
    if intent in {"document_research", "mixed"} or not selected:
        add("downloads_documents", "existing cold index can retrieve document and agent-asset evidence")
    if "downloads" in goal.lower() or "下载" in goal:
        add("downloads_documents", "goal explicitly mentions Downloads or downloaded files")
    if "workflow" in goal.lower() or "上下文" in goal or "热包" in goal:
        add("workflow_docs", "goal mentions workflow/context; local docs are high-signal")

    if not selected and "downloads_documents" in available:
        add("downloads_documents", "fallback to the existing indexed local source")
    return selected[:4], reasons


def grep_provider_scores(grep_route_probe: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(grep_route_probe, dict):
        return {}
    scores = grep_route_probe.get("provider_scores")
    return scores if isinstance(scores, dict) else {}


def source_is_available(candidate: dict[str, Any]) -> bool:
    source_id = candidate.get("source_id")
    status = candidate.get("status")
    if source_id == "codebase_memory":
        return status == "indexed"
    return status != "missing"


def refresh_action_for(source_id: str, candidates: list[dict[str, Any]]) -> str:
    candidate = next((record for record in candidates if record["source_id"] == source_id), {})
    status = candidate.get("status")
    if source_id == "downloads_documents":
        return "reuse_existing_index" if status == "indexed" else "build_index_from_manifests"
    if source_id == "git_repositories":
        return "reuse_project_index" if status == "indexed" else "refresh_provider_manifest"
    if source_id == "codebase_memory":
        return "reuse_codebase_memory_provider" if status == "indexed" else "run_codebase-memory-index_or_install_codebase-memory-mcp"
    if source_id == "codex_sessions":
        return "reuse_session_index" if status == "indexed" else "refresh_provider_manifest"
    return "read_current_files"


def plan_queries(goal: str, intent: str, entities: list[str], terms: list[str]) -> list[str]:
    queries = [goal]
    if entities:
        queries.append(" ".join(entities[:3]))
    if intent in {"project_code", "mixed"}:
        queries.append(" ".join(["项目", "架构", "构建", "实现", *terms[:3]]))
        queries.append("recommendation system local project architecture")
    if intent in {"document_research", "mixed"}:
        queries.append(" ".join(["资料", "文档", "研究", *terms[:3]]))
    if intent in {"agent_history", "mixed"}:
        queries.append(" ".join(["Codex", "会话", "历史", *terms[:3]]))
    if intent in {"workflow_handoff", "mixed"}:
        queries.append(" ".join(["workflow", "handoff", "context", "MCP", *terms[:3]]))
    if "个人助手" in goal or "长期记忆" in goal:
        queries.append("个人助手 长期记忆 agent assistant memory workflow skill")
        queries.append("task planner skill workflow memory")
    return list(dict.fromkeys(query for query in queries if query.strip()))[:6]


def retrieve_candidates_for_plan(out_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if "downloads_documents" in plan["selected_sources"]:
        for query in plan["queries"]:
            try:
                result = search_cold_index(out_root, query, limit=max(20, plan["constraints"]["max_context_sources"] * 4))
            except FileNotFoundError:
                continue
            for source in result["sources"]:
                candidate = dict(source)
                candidate["source_group"] = "downloads_documents"
                candidate["matched_queries"] = [query]
                candidate["retrieval_query"] = query
                candidates.append(candidate)

    if "workflow_docs" in plan["selected_sources"]:
        candidates.extend(workflow_doc_candidates(out_root, plan))

    if "git_repositories" in plan["selected_sources"]:
        candidates.extend(project_index_candidates(out_root, plan))
        candidates.extend(project_candidates(out_root, plan))
    if "codebase_memory" in plan["selected_sources"]:
        candidates.extend(codebase_memory_candidates(out_root, plan))
    if "codex_sessions" in plan["selected_sources"]:
        candidates.extend(session_index_candidates(out_root, plan))
        candidates.extend(session_candidates(out_root, plan))

    candidates.extend(semantic_index_candidates(out_root, plan))
    return filter_records_for_access(out_root, candidates, audit_action="resolver_filter_candidates")


def filter_avoided_candidates(
    candidates: list[dict[str, Any]],
    avoid_sources: list[str],
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    avoid = {value.strip() for value in avoid_sources if value and value.strip()}
    plan["avoid_sources"] = sorted(avoid)
    if not avoid:
        plan["avoid_stats"] = {"filtered_candidates": 0}
        return candidates
    kept = [candidate for candidate in candidates if not candidate_matches_avoid(candidate, avoid)]
    plan["avoid_stats"] = {
        "filtered_candidates": len(candidates) - len(kept),
        "candidates_before_avoid": len(candidates),
        "candidates_after_avoid": len(kept),
    }
    return kept


def candidate_matches_avoid(candidate: dict[str, Any], avoid: set[str]) -> bool:
    values = candidate_identity_values(candidate)
    if values & avoid:
        return True
    path = str(candidate.get("path") or "")
    project_path = str(candidate.get("project_path") or "")
    for rejected in avoid:
        if project_path and rejected == project_path:
            return True
        if path and rejected and (path == rejected or path.startswith(f"{rejected}/")):
            return True
    return False


def candidate_identity_values(candidate: dict[str, Any]) -> set[str]:
    values = set()
    for field in (
        "path",
        "relative_path",
        "source_id",
        "source_chunk_id",
        "doc_id",
        "project_id",
        "project_path",
        "project_name",
    ):
        value = candidate.get(field)
        if value:
            values.add(str(value))
    return values


def semantic_index_candidates(out_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    source_kinds = semantic_source_kinds_for(plan["selected_sources"])
    if not source_kinds or not semantic_index_path_for(out_root).exists():
        return []
    candidates = []
    for query in plan["queries"]:
        try:
            result = search_semantic_index(
                out_root,
                query,
                limit=max(20, plan["constraints"]["max_context_sources"] * 4),
                source_kinds=source_kinds,
            )
        except Exception as exc:  # noqa: BLE001 - semantic retrieval is an optional side channel.
            plan.setdefault("semantic_retrieval_errors", []).append(f"{type(exc).__name__}: {exc}")
            continue
        if result.get("skipped_reason"):
            plan.setdefault("semantic_retrieval_skips", []).append(
                f"{query}: {result['skipped_reason']}"
            )
        elif result.get("retrieval_mode"):
            plan.setdefault("semantic_retrieval_modes", []).append(
                f"{query}: {result['retrieval_mode']}"
            )
        if result.get("ann_fallback_reason"):
            plan.setdefault("semantic_ann_fallbacks", []).append(
                f"{query}: {result['ann_fallback_reason']}"
            )
        if result.get("ann_cache_status"):
            plan.setdefault("semantic_ann_cache_statuses", []).append(
                f"{query}: {result['ann_cache_status']}"
            )
        for source in result["sources"]:
            candidate = dict(source)
            apply_semantic_resolver_weight(candidate, query)
            candidate["matched_queries"] = [query]
            candidate["retrieval_query"] = query
            candidate["retrieval_channel"] = "semantic_index"
            candidates.append(candidate)
    return candidates


def apply_semantic_resolver_weight(candidate: dict[str, Any], query: str) -> None:
    raw_score = float(candidate.get("score_parts", {}).get("semantic", candidate.get("score", 0.0)) or 0.0)
    has_support = semantic_has_lexical_support(candidate, query)
    weight = 0.85 if has_support else 0.35
    weighted = round(raw_score * weight, 6)
    candidate["semantic_lexical_support"] = has_support
    candidate["score_parts"] = {
        **candidate.get("score_parts", {}),
        "semantic_raw": round(raw_score, 6),
        "semantic": weighted,
    }
    candidate["score"] = weighted


def semantic_has_lexical_support(candidate: dict[str, Any], query: str) -> bool:
    terms = semantic_support_terms_for(query)
    haystack = " ".join(
        [
            str(candidate.get("path") or ""),
            str(candidate.get("relative_path") or ""),
            str(candidate.get("snippet") or ""),
        ]
    ).lower()
    hits = sum(1 for term in terms if term.lower() in haystack)
    return hits >= 2


def semantic_support_terms_for(query: str) -> list[str]:
    generic_terms = {
        "architecture",
        "build",
        "context",
        "local",
        "project",
        "system",
        "告诉我本地所有项目里如何构建个人推荐系统",
        "构建",
        "项目",
        "本地",
        "系统",
    }
    terms = [
        term
        for term in terms_for(query)
        if len(term) >= 3 and term.lower() not in generic_terms
    ]
    lower_query = query.lower()
    if "推荐" in lower_query or "recommend" in lower_query:
        terms.extend(["推荐", "recommendation", "recommender", "ranking", "feedback"])
    return list(dict.fromkeys(terms))


def semantic_source_kinds_for(selected_sources: list[str]) -> list[str]:
    kinds = []
    if "downloads_documents" in selected_sources:
        kinds.append("downloads")
    if "git_repositories" in selected_sources:
        kinds.append("projects")
    if "codex_sessions" in selected_sources:
        kinds.append("sessions")
    return kinds


def workflow_doc_candidates(out_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    terms = set(plan["keywords"])
    candidates = []
    for record in load_workflow_records(out_root):
        text = record.get("text") or ""
        haystack = f"{record.get('title', '')}\n{record.get('path', '')}\n{text}".lower()
        overlap = sum(1 for term in terms if term.lower() in haystack)
        if overlap == 0 and plan["intent"] not in {"workflow_handoff", "mixed", "project_code"}:
            continue
        score = min(1.0, 0.25 + overlap / max(len(terms), 1))
        candidates.append(
            {
                "type": "workflow_doc",
                "source_id": record.get("source_id"),
                "doc_id": record.get("workflow_id"),
                "path": record.get("path"),
                "relative_path": record.get("relative_path"),
                "provider": record.get("provider"),
                "workflow_id": record.get("workflow_id"),
                "title": record.get("title"),
                "score": round(score, 6),
                "score_parts": {"workflow": round(score, 6), "term_overlap": overlap},
                "snippet": snippet(text, 520),
                "source_group": "workflow_docs",
                "matched_queries": [plan["queries"][0]],
                "retrieval_query": plan["queries"][0],
            }
        )
    return candidates


def project_candidates(out_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    terms = set(plan["keywords"])
    has_project_index = project_index_path_for(out_root).exists()
    candidates = []
    for record in load_project_records(out_root):
        text = record.get("text") or ""
        haystack = f"{record.get('name', '')}\n{record.get('path', '')}\n{text}".lower()
        overlap = sum(1 for term in terms if term.lower() in haystack)
        if overlap == 0 and plan["intent"] not in {"project_code", "workflow_handoff", "mixed"}:
            continue
        score = min(1.0, 0.22 + overlap / max(len(terms), 1))
        if record.get("has_git"):
            score += 0.08
        if has_project_index:
            score *= 0.35
        candidates.append(
            {
                "type": "project_provider",
                "source_id": record.get("source_id"),
                "doc_id": record.get("project_id"),
                "path": record.get("path"),
                "relative_path": record.get("relative_path"),
                "provider": record.get("provider"),
                "project_id": record.get("project_id"),
                "score": round(min(score, 1.0), 6),
                "score_parts": {
                    "project": round(min(score, 1.0), 6),
                    "term_overlap": overlap,
                    "provider_fallback": 1.0 if has_project_index else 0.0,
                },
                "snippet": snippet(text or record.get("path", ""), 520),
                "source_group": "git_repositories",
                "matched_queries": matched_queries_for_text(plan["queries"], haystack),
                "retrieval_query": plan["queries"][0],
            }
        )
    return candidates


def project_index_candidates(out_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not project_index_path_for(out_root).exists():
        return []
    candidates = []
    for query in plan["queries"]:
        try:
            result = search_project_index(out_root, query, limit=max(20, plan["constraints"]["max_context_sources"] * 4))
        except FileNotFoundError:
            continue
        for source in result["sources"]:
            candidate = dict(source)
            candidate["type"] = "project_code"
            candidate["source_group"] = "git_repositories"
            candidate["matched_queries"] = [query]
            candidate["retrieval_query"] = query
            candidates.append(candidate)
    return candidates


def codebase_memory_candidates(out_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for query in plan["queries"]:
        try:
            result = search_codebase_memory(
                out_root,
                query,
                limit=max(20, plan["constraints"]["max_context_sources"] * 4),
            )
        except Exception as exc:  # noqa: BLE001 - optional external provider must not break resolver.
            plan.setdefault("codebase_memory_errors", []).append(f"{query}: {type(exc).__name__}: {exc}")
            continue
        if result.get("status") not in {"ok", "no_matches"}:
            plan.setdefault("codebase_memory_errors", []).append(f"{query}: {result.get('status')}")
        for source in result.get("sources") or []:
            candidate = dict(source)
            candidate["type"] = "codebase_memory"
            candidate["source_group"] = "codebase_memory"
            candidate["matched_queries"] = [query]
            candidate["retrieval_query"] = query
            candidate["retrieval_channel"] = "codebase_memory_search_code"
            candidates.append(candidate)
    return candidates


def session_index_candidates(out_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not session_index_path_for(out_root).exists():
        return []
    candidates = []
    for query in plan["queries"]:
        try:
            result = search_session_index(out_root, query, limit=max(20, plan["constraints"]["max_context_sources"] * 4))
        except FileNotFoundError:
            continue
        for source in result["sources"]:
            candidate = dict(source)
            candidate["type"] = "session_chunk"
            candidate["source_group"] = "codex_sessions"
            candidate["matched_queries"] = [query]
            candidate["retrieval_query"] = query
            candidates.append(candidate)
    return candidates


def session_candidates(out_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    terms = set(plan["keywords"])
    has_session_index = session_index_path_for(out_root).exists()
    content_terms = {
        term
        for term in set(plan.get("entities", [])) | terms
        if term.lower() not in GENERIC_SESSION_TERMS
    }
    scoring_terms = content_terms or terms
    candidates = []
    for record in load_session_records(out_root):
        text = record.get("text") or ""
        haystack = f"{record.get('cwd', '')}\n{record.get('path', '')}\n{text}".lower()
        overlap = sum(1 for term in scoring_terms if term.lower() in haystack)
        if overlap == 0:
            continue
        score = min(1.0, 0.18 + overlap / max(len(scoring_terms), 1))
        if record.get("cwd"):
            score += 0.05
        if has_session_index:
            score *= 0.35
        candidates.append(
            {
                "type": "session_provider",
                "source_id": record.get("source_id"),
                "doc_id": record.get("session_id"),
                "path": record.get("path"),
                "relative_path": record.get("relative_path"),
                "provider": record.get("provider"),
                "session_id": record.get("session_id"),
                "thread_name": record.get("thread_name"),
                "score": round(min(score, 1.0), 6),
                "score_parts": {
                    "session": round(min(score, 1.0), 6),
                    "term_overlap": overlap,
                    "provider_fallback": 1.0 if has_session_index else 0.0,
                },
                "snippet": snippet(text or record.get("first_user_message", ""), 520),
                "source_group": "codex_sessions",
                "matched_queries": matched_queries_for_text(plan["queries"], haystack),
                "retrieval_query": plan["queries"][0],
            }
        )
    return candidates


def matched_queries_for_text(queries: list[str], haystack: str) -> list[str]:
    matched = []
    for query in queries:
        terms = terms_for(query)
        if any(term.lower() in haystack for term in terms):
            matched.append(query)
    return matched[:3] or queries[:1]


def fuse_candidates(
    candidates: list[dict[str, Any]],
    limit: int,
    *,
    feedback_model: dict[str, Any] | None = None,
    route_selector_model: dict[str, Any] | None = None,
    query_family: str | None = None,
) -> list[dict[str, Any]]:
    feedback_model = feedback_model or {}
    route_selector_model = route_selector_model or {}
    merged: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate.get("source_chunk_id") or candidate.get("source_id") or candidate["path"]
        existing = merged.get(key)
        if not existing:
            merged[key] = normalize_candidate(candidate)
            continue
        existing["matched_queries"] = sorted(
            set(existing.get("matched_queries", [])) | set(candidate.get("matched_queries", []))
        )
        existing["retrieval_channels"] = sorted(
            set(existing.get("retrieval_channels", [])) | set(retrieval_channels_for(candidate))
        )
        existing["score_parts"] = {**existing.get("score_parts", {}), **candidate.get("score_parts", {})}
        if float(candidate.get("score", 0.0)) > float(existing.get("score", 0.0)):
            for field in ("score", "snippet", "retrieval_query", "retrieval_channel"):
                existing[field] = candidate.get(field)

    scored = []
    for candidate in merged.values():
        retrieval = float(candidate.get("score", 0.0))
        query_coverage = query_coverage_for(candidate)
        source_weight = source_prior(candidate)
        feedback_parts = feedback_boost_parts(feedback_model, candidate, query_family=query_family)
        feedback = feedback_parts["total"]
        route_selector_parts = route_selector_boost_parts(route_selector_model, candidate, query_family=query_family)
        route_selector_prior = route_selector_parts["total"]
        resolver_score = round(
            max(0.0, min(1.0, retrieval * 0.72 + query_coverage * 0.18 + source_weight * 0.10 + feedback + route_selector_prior)),
            6,
        )
        candidate["resolver_score_parts"] = {
            "retrieval": round(retrieval, 6),
            "query_coverage": round(query_coverage, 6),
            "source_prior": round(source_weight, 6),
            "feedback": round(feedback, 6),
            "feedback_source": feedback_parts["source"],
            "feedback_route": feedback_parts["route"],
            "feedback_query_family_source": feedback_parts["query_family_source"],
            "feedback_query_family_route": feedback_parts["query_family_route"],
            "feedback_pairwise_elo_source": feedback_parts["pairwise_elo_source"],
            "feedback_query_family_pairwise_elo_source": feedback_parts["query_family_pairwise_elo_source"],
            "feedback_pairwise_bradley_terry_source": feedback_parts["pairwise_bradley_terry_source"],
            "feedback_query_family_pairwise_bradley_terry_source": feedback_parts["query_family_pairwise_bradley_terry_source"],
            "route_selector": route_selector_prior,
            "route_selector_global": route_selector_parts["global"],
            "route_selector_source": route_selector_parts["source"],
            "route_selector_query_family": route_selector_parts["query_family"],
        }
        candidate["score"] = resolver_score
        candidate["why_selected"] = why_selected(candidate)
        scored.append(candidate)

    scored.sort(key=candidate_rank_key)
    return diversify(scored, limit=limit)[:limit]


def candidate_rank_key(source: dict[str, Any]) -> tuple[float, float, float, str, str]:
    parts = source.get("resolver_score_parts") or {}
    return (
        -float(source.get("score") or 0.0),
        -float(parts.get("feedback") or 0.0),
        -float(parts.get("route_selector") or 0.0),
        str(source.get("source_group") or ""),
        str(source.get("path") or ""),
    )


def query_coverage_for(candidate: dict[str, Any]) -> float:
    channels = set(retrieval_channels_for(candidate))
    if channels == {"semantic_index"} and not candidate.get("semantic_lexical_support"):
        return 0.0
    return min(1.0, len(candidate.get("matched_queries", [])) / 3.0)


def normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    source = dict(candidate)
    source.setdefault("score_parts", {})
    source.setdefault("matched_queries", [])
    source.setdefault("snippet", "")
    source.setdefault("retrieval_channels", retrieval_channels_for(source))
    return source


def retrieval_channels_for(candidate: dict[str, Any]) -> list[str]:
    existing = candidate.get("retrieval_channels")
    if isinstance(existing, list):
        return [str(channel) for channel in existing if channel]
    channel = candidate.get("retrieval_channel") or candidate.get("provider") or candidate.get("type")
    return [str(channel)] if channel else []


def source_prior(candidate: dict[str, Any]) -> float:
    group = candidate.get("source_group")
    if group == "workflow_docs":
        return 0.8
    if group in {"downloads_documents", "git_repositories", "codebase_memory", "codex_sessions"}:
        return 0.6
    return 0.0


def why_selected(candidate: dict[str, Any]) -> str:
    queries = ", ".join(candidate.get("matched_queries", [])[:2])
    group = candidate.get("source_group", "unknown")
    parts = candidate.get("resolver_score_parts", {})
    return (
        f"selected from {group}; matched {len(candidate.get('matched_queries', []))} resolver query/query group(s)"
        f"; retrieval={parts.get('retrieval', 0)}; query_coverage={parts.get('query_coverage', 0)}"
        f"; feedback={parts.get('feedback', 0)}"
        f"; route_selector={parts.get('route_selector', 0)}"
        f"; queries={queries}"
    )


def diversify(scored: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    primary = []
    overflow = []
    seen_paths = set()
    seen_groups: dict[str, int] = {}
    seen_projects: dict[str, int] = {}
    project_cap = project_diversity_cap_for(limit)
    for source in scored:
        path = source.get("path")
        group = source.get("source_group", "unknown")
        project_key = source.get("project_id") or source.get("project_path")
        if path in seen_paths:
            overflow.append(source)
            continue
        seen_paths.add(path)
        if group == "git_repositories" and project_key:
            if seen_projects.get(project_key, 0) >= project_cap:
                overflow.append(source)
                continue
            seen_projects[project_key] = seen_projects.get(project_key, 0) + 1
        if group != "git_repositories" and seen_groups.get(group, 0) >= 6:
            overflow.append(source)
            continue
        seen_groups[group] = seen_groups.get(group, 0) + 1
        primary.append(source)
    return primary + overflow


def project_diversity_cap_for(limit: int) -> int:
    normalized_limit = max(1, int(limit))
    if normalized_limit <= 4:
        return PROJECT_DIVERSITY_CAP
    if normalized_limit <= 12:
        return max(PROJECT_DIVERSITY_CAP, 3)
    return min(8, max(3, normalized_limit // 10 + 2))


def retrieval_stats(candidates: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    for candidate in candidates:
        group = candidate.get("source_group", "unknown")
        by_source[group] = by_source.get(group, 0) + 1
        channel = candidate.get("retrieval_channel") or candidate.get("provider") or candidate.get("type") or "unknown"
        by_channel[channel] = by_channel.get(channel, 0) + 1
    return {
        "candidates_considered": len(candidates),
        "sources_included": len(sources),
        "candidate_count_by_source": by_source,
        "candidate_count_by_channel": by_channel,
    }


def render_context(goal: str, created_at: str, plan: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    lines = [
        "---",
        f"resolver_version: {RESOLVER_VERSION}",
        f"route: {RESOLVER_ROUTE}",
        f"goal: {goal}",
        f"created_at: {created_at}",
        "---",
        "",
        "# Task",
        "",
        goal,
        "",
        "# Resolution Plan",
        "",
        f"- Intent: `{plan['intent']}`",
        f"- Source scope: `{plan['source_scope']}`",
        f"- Selected sources: `{', '.join(plan['selected_sources'])}`",
        f"- Queries: {', '.join(f'`{query}`' for query in plan['queries'])}",
        "",
        "# Must Know",
        "",
        f"- Resolver used `{RESOLVER_ROUTE}` deterministic rules.",
        f"- Candidate sources considered: {plan.get('retrieval_stats', {}).get('candidates_considered', 0)}",
        f"- Sources included: {len(sources)}",
        "- This is a task-specific hot context pack, not a full-disk index dump.",
    ]
    grep_probe = plan.get("grep_route_probe") if isinstance(plan.get("grep_route_probe"), dict) else {}
    grep_scores = grep_probe.get("provider_scores") if isinstance(grep_probe, dict) else {}
    if isinstance(grep_scores, dict) and grep_scores:
        routed = ", ".join(
            f"{source_id}={stats.get('score')}"
            for source_id, stats in list(grep_scores.items())[:4]
            if isinstance(stats, dict)
        )
        lines.append(f"- Grep route probe: `{grep_probe.get('engine')}` matched provider scores: {routed}.")
    else:
        lines.append("- Grep route probe found no strong deterministic provider matches.")
    lines.extend(["", "# Top Sources", ""])
    if sources:
        for source in sources:
            lines.append(
                f"- `{source['path']}` ({source.get('source_group')}, score={source['score']}, why={source['why_selected']})"
            )
    else:
        lines.append("- No source matched the resolver plan.")

    lines.extend(["", "# Source Notes", ""])
    if sources:
        for source in sources[:8]:
            lines.append(f"> {source.get('snippet', '')}")
            lines.append(">")
            lines.append(f"> Source: `{source['path']}`")
            lines.append("")
    else:
        lines.append("- The resolver produced no snippets; inspect `resolution_plan.json` for selected sources and queries.")

    lines.extend(["# Limitations", ""])
    lines.append("- v0.5 resolver uses deterministic routing, local retrieval, feedback priors, and retrieval-eval route priors; no LLM-only router is used.")
    lines.append("- Git repositories use provider cards plus selected code chunks; sessions use provider cards plus transcript chunks when `indexes/sessions.sqlite` exists.")
    semantic_count = plan.get("retrieval_stats", {}).get("candidate_count_by_channel", {}).get("semantic_index", 0)
    if semantic_count:
        modes = ", ".join(sorted(set(str(mode).split(": ", 1)[-1] for mode in plan.get("semantic_retrieval_modes", [])))) or "semantic_exact_vector_scan"
        lines.append(f"- Precomputed semantic index contributed {semantic_count} candidate(s) using `{modes}`.")
        cache_statuses = ", ".join(sorted(set(str(status).split(": ", 1)[-1] for status in plan.get("semantic_ann_cache_statuses", []))))
        if cache_statuses:
            lines.append(f"- Semantic ANN cache status: `{cache_statuses}`.")
        if plan.get("semantic_ann_fallbacks"):
            lines.append("- ANN fallback occurred for at least one query; inspect `resolution_plan.json` for details.")
    elif plan.get("retrieval_config", {}).get("rerank_backend") == FASTEMBED_BACKEND_ID:
        lines.append("- Query-time fastembed rerank may be used on a small candidate pool; precomputed semantic ANN may be unavailable for this source.")
    else:
        lines.append("- ANN search may be unavailable without optional hnswlib; OCR and audio/video transcription are not implemented in this resolver.")

    lines.extend(["", "# Recommended Next Actions", ""])
    if sources:
        lines.append("- Read the top source paths above before promoting anything into long-term memory.")
    else:
        lines.append("- Build or refresh a cold index for the expected source before trying this goal again.")
    lines.append("- Record feedback on useful or irrelevant sources so later ranking can adjust source priors.")
    lines.append("")
    return "\n".join(lines)
