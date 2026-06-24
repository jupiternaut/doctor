from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .evidence import attach_evidence_records
from .feedback_model import feedback_boost_parts, load_feedback_model, query_family_for_text
from .io import ensure_dir, write_jsonl, write_text
from .pack import slugify, snippet
from .retrieval_backends import (
    backend_meta,
    default_retrieval_config,
    embed_documents,
    get_embedding_backend,
    query_terms,
)

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is present in the packaged environment.
    yaml = None


VAULT_INDEX_VERSION = "0.1"
VAULT_RESOLVER_VERSION = "0.1"
OKF_VERSION = "0.1"
CANONICAL_CONCEPT_DIRS = ("projects", "entities", "workflows", "claims", "contradictions", "failures")
OKF_REQUIRED_FRONTMATTER_FIELDS = ("type",)
OKF_RECOMMENDED_FRONTMATTER_FIELDS = ("title", "description", "resource", "tags", "timestamp")
DOCTOR_REQUIRED_FRONTMATTER_FIELDS = (
    "type",
    "title",
    "description",
    "timestamp",
    "id",
    "aliases",
    "citations",
    "freshness",
    "confidence",
    "source_hashes",
)
SKIP_SOURCE_DIRS = {
    ".bun",
    ".git",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}
TEXT_SUFFIXES = {
    ".css",
    ".go",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".rs",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SOURCE_PRIORITY_NAMES = {
    "README.md",
    "readme.md",
    "AGENTS.md",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
}
RESUME_INTENT_MARKERS = ("简历", "求职", "面试", "包装", "作品集", "resume", "job", "portfolio", "interview")
DEFAULT_BASELINE_GOALS = (
    "我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些",
    "开源往事如何在番茄爆火，面向的读者是谁",
)
PRIMARY_RESUME_PRIOR = {
    "project-plm": 0.36,
    "project-drama": 0.34,
    "project-codex-plus-plus": 0.33,
    "project-gugu": 0.32,
    "project-doctor": 0.18,
}
PORTFOLIO_RESUME_GUARD = {
    "project-plm": 1.5,
    "project-drama": 1.25,
    "project-codex-plus-plus": 1.0,
    "project-gugu": 0.55,
}
PORTFOLIO_PROJECT_IDS = set(PORTFOLIO_RESUME_GUARD)


def vault_index_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "indexes" / "vault.sqlite"


def knowledge_index_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "indexes" / "knowledge.sqlite"


def knowledge_edges_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "indexes" / "knowledge_edges.jsonl"


def build_vault_index(out_root: Path) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    vault = out_root / "vault"
    if not vault.exists():
        raise FileNotFoundError(f"vault not found: {vault}")

    concepts = collect_vault_concepts(out_root)
    retrieval_config = default_retrieval_config()
    backend = get_embedding_backend(retrieval_config)
    embeddings = embed_documents(
        backend,
        [embedding_text_for(concept) for concept in concepts],
    )
    for concept, embedding in zip(concepts, embeddings):
        concept["embedding_json"] = embedding

    db_path = vault_index_path_for(out_root)
    ensure_dir(db_path.parent)
    reset_sqlite_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        fts_enabled = create_schema(conn)
        for concept in concepts:
            insert_concept(conn, concept, fts_enabled=fts_enabled)
        insert_shared_tag_edges(conn, concepts)
        for key, value in {
            "vault_index_version": VAULT_INDEX_VERSION,
            "built_at": datetime.now().astimezone().isoformat(),
            "concepts": str(len(concepts)),
            "fts_enabled": json.dumps(fts_enabled),
            **backend_meta(retrieval_config),
        }.items():
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()

    knowledge_path = knowledge_index_path_for(out_root)
    reset_sqlite_database(knowledge_path)
    shutil.copy2(db_path, knowledge_path)
    edges_path = export_graph_edges_jsonl(db_path, knowledge_edges_path_for(out_root))
    report_path = write_vault_index_report(out_root, concepts, db_path, fts_enabled, knowledge_path=knowledge_path, edges_path=edges_path)
    return {
        "status": "ok",
        "vault_index_version": VAULT_INDEX_VERSION,
        "index_path": str(db_path),
        "knowledge_index_path": str(knowledge_path),
        "knowledge_edges_jsonl_path": str(edges_path),
        "concepts_indexed": len(concepts),
        "fts_enabled": fts_enabled,
        "report_path": str(report_path),
        **backend_meta(retrieval_config),
    }


def resolve_vault_context(
    out_root: Path,
    goal: str,
    *,
    limit: int = 8,
    mode: str = "fast",
    continue_from: Path | None = None,
    feedback: str = "neutral",
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    limit = max(1, limit)
    if not vault_index_path_for(out_root).exists():
        build_vault_index(out_root)

    if continue_from:
        updated = update_anytime_state(continue_from.expanduser().resolve(), feedback=feedback)
        if feedback == "satisfied":
            return updated

    concepts = load_indexed_concepts(out_root)
    token_report = estimate_vault_tokens(out_root, concepts)
    feedback_model = load_feedback_model(out_root)
    fts_scores = search_vault_fts(out_root, vault_terms(goal), limit=max(limit * 3, len(concepts)))
    ranked = rank_vault_concepts(concepts, goal, feedback_model=feedback_model, fts_scores=fts_scores)
    selected = ranked[:limit]
    misses = missed_concepts(ranked, selected_ids={item["concept_id"] for item in selected}, limit=limit)

    now = datetime.now().astimezone()
    task_id = f"{slugify(goal)}-vault-{mode}-{now.strftime('%Y%m%d%H%M%S%f')}"
    pack_dir = ensure_dir(out_root / "packs" / task_id)
    context_path = pack_dir / "context.md"
    sources_path = pack_dir / "sources.jsonl"
    manifest_path = pack_dir / "manifest.json"
    plan_path = pack_dir / "vault_resolution_plan.json"
    state_dir = ensure_dir(out_root / "vault" / "anytime" / task_id)
    state_path = state_dir / "state.json"

    sources = attach_evidence_records([source_for_candidate(item, goal) for item in selected], goal=goal)
    plan = {
        "vault_resolver_version": VAULT_RESOLVER_VERSION,
        "goal": goal,
        "mode": mode,
        "query_family": query_family_for_text(goal),
        "terms": vault_terms(goal),
        "channels": ["catalog_prior", "exact_terms", "fts", "vector", "graph_edges", "feedback_weights"],
        "token_report": token_report,
        "misses": misses,
        "ranked_count": len(ranked),
        "selected_count": len(selected),
    }
    state = build_anytime_state(task_id, goal, mode, selected, ranked[limit:], token_report)
    manifest = {
        "vault_resolver_version": VAULT_RESOLVER_VERSION,
        "task_id": task_id,
        "goal": goal,
        "mode": mode,
        "created_at": now.isoformat(),
        "sources_included": len(sources),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "vault_resolution_plan_json_path": str(plan_path),
        "anytime_state_json_path": str(state_path),
        "token_report": token_report,
    }

    write_jsonl(sources_path, sources)
    write_text(plan_path, json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(context_path, render_vault_context(goal, now.isoformat(), selected, misses, token_report, state_path))

    return {
        "status": "ok",
        "vault_resolver_version": VAULT_RESOLVER_VERSION,
        "task_id": task_id,
        "mode": mode,
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "vault_resolution_plan_json_path": str(plan_path),
        "anytime_state_json_path": str(state_path),
        "sources_included": len(sources),
        "top_concepts": [
            {
                "concept_id": item["concept_id"],
                "title": item["title"],
                "score": item["score"],
                "score_parts": item["score_parts"],
            }
            for item in selected
        ],
        "misses": misses,
        "token_report": token_report,
    }


def run_vault_anytime_step(
    out_root: Path,
    state_path: Path,
    *,
    feedback: str = "not_right",
    limit: int = 12,
    max_files_per_root: int = 80,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    state_path = state_path.expanduser().resolve()
    if not state_path.exists():
        raise FileNotFoundError(f"anytime state not found: {state_path}")

    if feedback == "satisfied":
        return update_anytime_state(state_path, feedback=feedback)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    goal = str(state.get("goal") or "")
    terms = vault_terms(goal)
    round_number = int(state.get("expansion_round") or 0) + 1
    expanded_sources = rank_anytime_source_files(
        state,
        terms,
        limit=max(1, limit),
        max_files_per_root=max(1, max_files_per_root),
    )
    now = datetime.now().astimezone()
    task_id = f"{slugify(goal)}-vault-anytime-r{round_number}-{now.strftime('%Y%m%d%H%M%S%f')}"
    pack_dir = ensure_dir(out_root / "packs" / task_id)
    context_path = pack_dir / "context.md"
    sources_path = pack_dir / "sources.jsonl"
    manifest_path = pack_dir / "manifest.json"
    step_path = pack_dir / "vault_anytime_step.json"

    sources = attach_evidence_records(expanded_sources, goal=goal)
    step = {
        "vault_resolver_version": VAULT_RESOLVER_VERSION,
        "task_id": task_id,
        "goal": goal,
        "created_at": now.isoformat(),
        "feedback": feedback,
        "expansion_round": round_number,
        "strategy": "bounded_source_expansion_after_fast_answer",
        "max_files_per_root": max_files_per_root,
        "sources_included": len(sources),
        "previous_state_json_path": str(state_path),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
    }
    manifest = {
        **step,
        "mode": "anytime_step",
        "state_json_path": str(state_path),
    }

    write_jsonl(sources_path, sources)
    write_text(step_path, json.dumps(step, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(context_path, render_anytime_step_context(goal, now.isoformat(), state, expanded_sources, state_path))

    state.setdefault("feedback_history", []).append(
        {
            "feedback": feedback,
            "recorded_at": now.isoformat(),
            "expansion_round": round_number,
            "context_md_path": str(context_path),
            "sources_jsonl_path": str(sources_path),
        }
    )
    state["last_feedback"] = feedback
    state["updated_at"] = now.isoformat()
    state["status"] = "expanded_step_ready" if expanded_sources else "exhausted"
    state["expansion_round"] = round_number
    state["latest_expansion"] = {
        "task_id": task_id,
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "sources_included": len(sources),
    }
    state["expanded_sources"] = [compact_source_candidate(item) for item in expanded_sources]
    state["next_action"] = "review_expanded_context_then_stop_or_request_another_step"
    write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    return {
        "status": state["status"],
        "task_id": task_id,
        "expansion_round": round_number,
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "vault_anytime_step_json_path": str(step_path),
        "anytime_state_json_path": str(state_path),
        "sources_included": len(sources),
        "top_sources": [
            {
                "path": item["path"],
                "project_id": item.get("project_id"),
                "title": item.get("title"),
                "score": item.get("score"),
                "score_parts": item.get("score_parts"),
            }
            for item in expanded_sources
        ],
    }


def search_vault_concepts(out_root: Path, query: str, *, limit: int = 8) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    if not vault_index_path_for(out_root).exists():
        build_vault_index(out_root)
    concepts = load_indexed_concepts(out_root)
    feedback_model = load_feedback_model(out_root)
    fts_scores = search_vault_fts(out_root, vault_terms(query), limit=max(limit * 3, len(concepts)))
    ranked = rank_vault_concepts(concepts, query, feedback_model=feedback_model, fts_scores=fts_scores)
    selected = ranked[: max(1, limit)]
    return {
        "status": "ok",
        "query": query,
        "limit": limit,
        "sources": [source_for_candidate(item, query) for item in selected],
        "top_concepts": [
            {
                "concept_id": item["concept_id"],
                "title": item["title"],
                "score": item["score"],
                "score_parts": item["score_parts"],
                "freshness": item.get("freshness"),
                "confidence": item.get("confidence"),
            }
            for item in selected
        ],
    }


def run_vault_check(out_root: Path, *, rebuild: bool = False) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    vault = out_root / "vault"
    issues: list[dict[str, Any]] = []
    if not vault.exists():
        issues.append({"severity": "error", "path": str(vault), "message": "vault directory is missing"})
        report_path = write_vault_check_report(out_root, issues, {}, rebuild=rebuild)
        return {"status": "error", "issues": issues, "report_path": str(report_path)}

    all_markdown_files = sorted(vault.rglob("*.md"))
    validate_okf_bundle(vault, all_markdown_files, issues)

    if rebuild:
        build_vault_index(out_root)

    concept_files = [path for dirname in CANONICAL_CONCEPT_DIRS for path in sorted((vault / dirname).glob("*.md"))]
    for path in concept_files:
        frontmatter, _body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        missing = [field for field in DOCTOR_REQUIRED_FRONTMATTER_FIELDS if field not in frontmatter]
        if missing:
            issues.append(
                {
                    "severity": "error",
                    "path": str(path),
                    "message": "missing required Doctor extension frontmatter fields",
                    "fields": missing,
                }
            )
        if frontmatter.get("id") and str(frontmatter["id"]) != path.stem:
            issues.append(
                {
                    "severity": "warning",
                    "path": str(path),
                    "message": "frontmatter id does not match filename stem",
                    "id": str(frontmatter["id"]),
                    "stem": path.stem,
                }
            )
        if "confidence" in frontmatter and not isinstance(frontmatter["confidence"], int | float):
            issues.append({"severity": "error", "path": str(path), "message": "confidence must be numeric"})
        if "freshness" in frontmatter and not isinstance(frontmatter["freshness"], dict):
            issues.append({"severity": "error", "path": str(path), "message": "freshness must be an object"})

    db_path = vault_index_path_for(out_root)
    db_meta: dict[str, str] = {}
    db_concepts = 0
    if not db_path.exists():
        issues.append({"severity": "warning", "path": str(db_path), "message": "vault index is missing; run vault-index or vault-check --rebuild"})
    else:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            db_concepts = int(conn.execute("SELECT count(*) FROM concepts").fetchone()[0])
            db_meta = {str(row["key"]): str(row["value"]) for row in conn.execute("SELECT key, value FROM meta").fetchall()}
        finally:
            conn.close()
        if db_concepts != len(concept_files):
            issues.append(
                {
                    "severity": "warning",
                    "path": str(db_path),
                    "message": "index concept count differs from canonical Markdown concept count",
                    "index_concepts": db_concepts,
                    "markdown_concepts": len(concept_files),
                }
            )
        current_backend = backend_meta(default_retrieval_config())
        for key in ("embedding_backend", "embedding_model", "embedding_dimensions", "ann_backend", "rerank_backend"):
            if db_meta.get(key) != current_backend.get(key):
                issues.append(
                    {
                        "severity": "warning",
                        "path": str(db_path),
                        "message": "vector metadata differs from current retrieval backend",
                        "field": key,
                        "indexed": db_meta.get(key),
                        "current": current_backend.get(key),
                    }
                )

    summary = {
        "vault": str(vault),
        "index_path": str(db_path),
        "okf_version": OKF_VERSION,
        "okf_markdown_files": len(all_markdown_files),
        "markdown_concepts": len(concept_files),
        "indexed_concepts": db_concepts,
        "issue_count": len(issues),
        "error_count": sum(1 for issue in issues if issue["severity"] == "error"),
        "warning_count": sum(1 for issue in issues if issue["severity"] == "warning"),
        "okf_required_frontmatter_fields": list(OKF_REQUIRED_FRONTMATTER_FIELDS),
        "okf_recommended_frontmatter_fields": list(OKF_RECOMMENDED_FRONTMATTER_FIELDS),
        "doctor_required_frontmatter_fields": list(DOCTOR_REQUIRED_FRONTMATTER_FIELDS),
        "index_meta": db_meta,
    }
    report_path = write_vault_check_report(out_root, issues, summary, rebuild=rebuild)
    status = "error" if summary["error_count"] else "warning" if summary["warning_count"] else "ok"
    return {"status": status, **summary, "issues": issues, "report_path": str(report_path)}


def validate_okf_bundle(vault: Path, markdown_files: list[Path], issues: list[dict[str, Any]]) -> None:
    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            issues.append({"severity": "error", "path": str(path), "message": f"cannot read markdown file: {exc}"})
            continue

        if path.name == "index.md":
            validate_okf_index(vault, path, text, issues)
            continue
        if path.name == "log.md":
            validate_okf_log(path, text, issues)
            continue

        if not text.startswith("---\n") or not has_frontmatter_end(text):
            issues.append(
                {
                    "severity": "error",
                    "path": str(path),
                    "message": "OKF concept document must start with parseable YAML frontmatter",
                }
            )
            continue
        frontmatter, _body = parse_frontmatter(text)
        if not str(frontmatter.get("type") or "").strip():
            issues.append(
                {
                    "severity": "error",
                    "path": str(path),
                    "message": "OKF concept document must contain a non-empty type field",
                }
            )


def validate_okf_index(vault: Path, path: Path, text: str, issues: list[dict[str, Any]]) -> None:
    has_frontmatter = text.startswith("---\n") and has_frontmatter_end(text)
    if has_frontmatter:
        frontmatter, _body = parse_frontmatter(text)
        if path.resolve() != (vault / "index.md").resolve():
            issues.append(
                {
                    "severity": "error",
                    "path": str(path),
                    "message": "OKF index.md files must not contain frontmatter outside the bundle root",
                }
            )
            return
        unexpected = sorted(set(frontmatter) - {"okf_version"})
        if unexpected:
            issues.append(
                {
                    "severity": "error",
                    "path": str(path),
                    "message": "root OKF index.md frontmatter may only declare okf_version",
                    "fields": unexpected,
                }
            )
        if str(frontmatter.get("okf_version") or "") != OKF_VERSION:
            issues.append(
                {
                    "severity": "error",
                    "path": str(path),
                    "message": "root OKF index.md must declare the supported okf_version",
                    "expected": OKF_VERSION,
                    "actual": str(frontmatter.get("okf_version") or ""),
                }
            )


def validate_okf_log(path: Path, text: str, issues: list[dict[str, Any]]) -> None:
    if text.startswith("---\n") and has_frontmatter_end(text):
        issues.append({"severity": "error", "path": str(path), "message": "OKF log.md files must not contain frontmatter"})
    if not re.search(r"^## \d{4}-\d{2}-\d{2}$", text, flags=re.MULTILINE):
        issues.append(
            {
                "severity": "error",
                "path": str(path),
                "message": "OKF log.md must use ISO date headings in YYYY-MM-DD form",
            }
        )


def has_frontmatter_end(text: str) -> bool:
    return any(line == "---" for line in text.splitlines()[1:])


def run_wiki_baseline_eval(out_root: Path, *, goals: list[str] | None = None, limit: int = 5) -> dict[str, Any]:
    from .grep_route import run_grep_route_probe

    out_root = out_root.expanduser().resolve()
    if not vault_index_path_for(out_root).exists():
        build_vault_index(out_root)
    concepts = load_indexed_concepts(out_root)
    token_report = estimate_vault_tokens(out_root, concepts)
    cases = []
    for goal in goals or list(DEFAULT_BASELINE_GOALS):
        terms = vault_terms(goal)
        raw_probe = run_grep_route_probe(out_root, goal, terms=terms, source_scope="all")
        vault_result = search_vault_concepts(out_root, goal, limit=limit)
        existing_packs = rank_existing_context_packs(out_root, goal, limit=limit)
        cases.append(
            {
                "goal": goal,
                "raw_file_search": {
                    "engine": raw_probe.get("engine"),
                    "provider_scores": raw_probe.get("provider_scores") or {},
                },
                "existing_context_packs": existing_packs,
                "vault_retrieval": {
                    "top_concepts": vault_result.get("top_concepts") or [],
                    "sources": [
                        {
                            "concept_id": source.get("concept_id"),
                            "title": source.get("title"),
                            "path": source.get("path"),
                            "score": source.get("score"),
                            "freshness_warning": source.get("freshness_warning"),
                        }
                        for source in vault_result.get("sources", [])
                    ],
                },
                "full_vault_context": {
                    "current_vault_token_estimate": token_report["current_vault_token_estimate"],
                    "canonical_concept_token_estimate": token_report["canonical_concept_token_estimate"],
                    "fits_128k_context": token_report["fits_128k_context"],
                    "fits_200k_context": token_report["fits_200k_context"],
                    "judgment": token_report["why_not_feed_all"],
                },
            }
        )
    report_id = datetime.now().astimezone().strftime("%Y%m%d%H%M%S%f")
    json_path = out_root / "reports" / f"llm_wiki_baseline_eval_{report_id}.json"
    md_path = out_root / "reports" / f"llm_wiki_baseline_eval_{report_id}.md"
    latest_path = out_root / "reports" / "llm_wiki_baseline_eval_latest.md"
    payload = {
        "baseline_eval_version": "0.1",
        "created_at": datetime.now().astimezone().isoformat(),
        "goals": [case["goal"] for case in cases],
        "token_report": token_report,
        "cases": cases,
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "latest_markdown_path": str(latest_path),
    }
    write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    markdown = render_wiki_baseline_eval_report(payload)
    write_text(md_path, markdown)
    write_text(latest_path, markdown)
    return {
        "status": "ok",
        "cases": len(cases),
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "latest_markdown_path": str(latest_path),
        "token_report": token_report,
    }


def collect_vault_concepts(out_root: Path) -> list[dict[str, Any]]:
    vault = out_root.expanduser().resolve() / "vault"
    concepts = []
    for dirname in CANONICAL_CONCEPT_DIRS:
        for path in sorted((vault / dirname).glob("*.md")):
            concepts.append(concept_from_markdown(path, vault))
    return concepts


def concept_from_markdown(path: Path, vault: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    concept_id = str(frontmatter.get("id") or path.stem)
    aliases = list_field(frontmatter.get("aliases"))
    tags = list_field(frontmatter.get("tags"))
    title = str(frontmatter.get("title") or path.stem)
    description = str(frontmatter.get("description") or first_paragraph(body) or title)
    source_path = str(frontmatter.get("source_path") or "")
    concept_type = str(frontmatter.get("type") or path.parent.name.rstrip("s") or "concept")
    token_estimate = estimate_tokens(text)
    citations = frontmatter.get("citations") if isinstance(frontmatter.get("citations"), list) else []
    frontmatter_hashes = [str(item) for item in list_field(frontmatter.get("source_hashes"))]
    body_hashes = re.findall(r"`([0-9a-f]{64})`", text)
    return {
        "concept_id": concept_id,
        "concept_type": concept_type,
        "title": title,
        "description": description,
        "aliases": aliases,
        "tags": tags,
        "citations": citations,
        "freshness": frontmatter.get("freshness") if isinstance(frontmatter.get("freshness"), dict) else {},
        "confidence": float(frontmatter.get("confidence") or 0.0),
        "source_path": source_path,
        "source_status": str(frontmatter.get("source_status") or ""),
        "canonical": bool(frontmatter.get("canonical", True)),
        "raw_files_read_only": bool(frontmatter.get("raw_files_read_only", True)),
        "path": str(path.resolve()),
        "relative_path": str(path.relative_to(vault)),
        "text": text,
        "body": body,
        "token_estimate": token_estimate,
        "source_hashes": list(dict.fromkeys(frontmatter_hashes + body_hashes)),
        "citation_count": text.count("| `"),
    }


def create_schema(conn: sqlite3.Connection) -> bool:
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE concepts (
          concept_id TEXT PRIMARY KEY,
          concept_type TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL,
          path TEXT NOT NULL,
          relative_path TEXT NOT NULL,
          source_path TEXT,
          source_status TEXT,
          token_estimate INTEGER NOT NULL,
          citation_count INTEGER NOT NULL,
          text TEXT NOT NULL,
          embedding_json TEXT NOT NULL,
          payload_json TEXT NOT NULL
        );

        CREATE INDEX idx_concepts_type ON concepts(concept_type);
        CREATE INDEX idx_concepts_source_path ON concepts(source_path);

        CREATE TABLE aliases (
          concept_id TEXT NOT NULL,
          alias TEXT NOT NULL,
          alias_kind TEXT NOT NULL,
          PRIMARY KEY(concept_id, alias, alias_kind)
        );

        CREATE INDEX idx_aliases_alias ON aliases(alias);

        CREATE TABLE graph_edges (
          edge_id TEXT PRIMARY KEY,
          source_id TEXT NOT NULL,
          edge_type TEXT NOT NULL,
          target_id TEXT NOT NULL,
          weight REAL NOT NULL,
          payload_json TEXT NOT NULL
        );

        CREATE INDEX idx_edges_source ON graph_edges(source_id);
        CREATE INDEX idx_edges_target ON graph_edges(target_id);

        CREATE TABLE citations (
          citation_id TEXT PRIMARY KEY,
          concept_id TEXT NOT NULL,
          path TEXT NOT NULL,
          sha256 TEXT,
          citation TEXT,
          payload_json TEXT NOT NULL
        );

        CREATE INDEX idx_citations_concept ON citations(concept_id);
        CREATE INDEX idx_citations_path ON citations(path);

        CREATE TABLE freshness (
          concept_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          checked_at TEXT,
          source_status TEXT,
          warning TEXT,
          confidence REAL NOT NULL,
          payload_json TEXT NOT NULL
        );

        CREATE INDEX idx_freshness_status ON freshness(status);

        CREATE TABLE claims (
          claim_id TEXT PRIMARY KEY,
          concept_id TEXT NOT NULL,
          claim_text TEXT NOT NULL,
          confidence REAL NOT NULL,
          payload_json TEXT NOT NULL
        );

        CREATE INDEX idx_claims_concept ON claims(concept_id);

        CREATE TABLE score_features (
          concept_id TEXT NOT NULL,
          feature TEXT NOT NULL,
          value REAL NOT NULL,
          payload_json TEXT NOT NULL,
          PRIMARY KEY(concept_id, feature)
        );
        """
    )
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE concepts_fts USING fts5(
              concept_id UNINDEXED,
              title,
              description,
              aliases,
              tags,
              source_path,
              text,
              tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )
        return True
    except sqlite3.OperationalError:
        return False


def insert_concept(conn: sqlite3.Connection, concept: dict[str, Any], *, fts_enabled: bool) -> None:
    payload_json = json.dumps(concept, ensure_ascii=False, sort_keys=True)
    conn.execute(
        """
        INSERT OR REPLACE INTO concepts(
          concept_id,
          concept_type,
          title,
          description,
          path,
          relative_path,
          source_path,
          source_status,
          token_estimate,
          citation_count,
          text,
          embedding_json,
          payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            concept["concept_id"],
            concept["concept_type"],
            concept["title"],
            concept["description"],
            concept["path"],
            concept["relative_path"],
            concept["source_path"],
            concept["source_status"],
            concept["token_estimate"],
            concept["citation_count"],
            concept["text"],
            concept["embedding_json"],
            payload_json,
        ),
    )
    for alias in concept["aliases"]:
        insert_alias(conn, concept["concept_id"], alias, "alias")
        insert_edge(conn, concept["concept_id"], "has_alias", f"alias:{alias.lower()}", 0.5, {"alias": alias})
    for tag in concept["tags"]:
        insert_alias(conn, concept["concept_id"], tag, "tag")
        insert_edge(conn, concept["concept_id"], "has_tag", f"tag:{tag.lower()}", 0.35, {"tag": tag})
    if concept.get("source_path"):
        insert_edge(
            conn,
            concept["concept_id"],
            "has_source_path",
            str(concept["source_path"]),
            1.0,
            {"source_path": concept["source_path"]},
        )
    for source_hash in concept.get("source_hashes") or []:
        insert_edge(conn, concept["concept_id"], "cites_hash", f"sha256:{source_hash}", 0.25, {"sha256": source_hash})
    for citation in concept.get("citations") or []:
        insert_citation(conn, concept, citation)
    insert_freshness(conn, concept)
    for claim_text in extract_claims(concept):
        insert_claim(conn, concept, claim_text)
    for feature, value in score_features_for_concept(concept).items():
        insert_score_feature(conn, concept["concept_id"], feature, value)
    if fts_enabled:
        conn.execute(
            """
            INSERT INTO concepts_fts(concept_id, title, description, aliases, tags, source_path, text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                concept["concept_id"],
                concept["title"],
                concept["description"],
                " ".join(concept["aliases"]),
                " ".join(concept["tags"]),
                concept["source_path"],
                concept["text"],
            ),
        )


def insert_alias(conn: sqlite3.Connection, concept_id: str, alias: str, alias_kind: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO aliases(concept_id, alias, alias_kind) VALUES (?, ?, ?)",
        (concept_id, alias, alias_kind),
    )


def insert_citation(conn: sqlite3.Connection, concept: dict[str, Any], citation: dict[str, Any]) -> None:
    path = str(citation.get("path") or "")
    sha256 = str(citation.get("sha256") or "")
    citation_text = str(citation.get("citation") or "")
    citation_id = "citation:" + hashlib.sha256(f"{concept['concept_id']}|{path}|{sha256}|{citation_text}".encode("utf-8")).hexdigest()
    payload = {"path": path, "sha256": sha256, "citation": citation_text}
    conn.execute(
        """
        INSERT OR REPLACE INTO citations(citation_id, concept_id, path, sha256, citation, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (citation_id, concept["concept_id"], path, sha256, citation_text, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )
    if path:
        insert_edge(conn, concept["concept_id"], "cites_source", path, 0.55, payload)


def insert_freshness(conn: sqlite3.Connection, concept: dict[str, Any]) -> None:
    freshness = concept.get("freshness") if isinstance(concept.get("freshness"), dict) else {}
    status = str(freshness.get("status") or "unknown")
    checked_at = str(freshness.get("checked_at") or "")
    warning = freshness_warning_for_concept(concept)
    payload = {
        "freshness": freshness,
        "source_status": concept.get("source_status") or "",
        "warning": warning,
        "confidence": concept.get("confidence", 0.0),
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO freshness(concept_id, status, checked_at, source_status, warning, confidence, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            concept["concept_id"],
            status,
            checked_at,
            str(concept.get("source_status") or ""),
            warning,
            float(concept.get("confidence") or 0.0),
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )
    if warning:
        insert_edge(conn, concept["concept_id"], "has_freshness_warning", warning, 0.4, payload)


def insert_claim(conn: sqlite3.Connection, concept: dict[str, Any], claim_text: str) -> None:
    claim_id = "claim:" + hashlib.sha256(f"{concept['concept_id']}|{claim_text}".encode("utf-8")).hexdigest()
    payload = {"claim": claim_text, "source_concept": concept["concept_id"]}
    conn.execute(
        """
        INSERT OR REPLACE INTO claims(claim_id, concept_id, claim_text, confidence, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (claim_id, concept["concept_id"], claim_text, float(concept.get("confidence") or 0.0), json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )
    insert_edge(conn, concept["concept_id"], "has_claim", claim_id, 0.35, payload)


def insert_score_feature(conn: sqlite3.Connection, concept_id: str, feature: str, value: float) -> None:
    payload = {"feature": feature, "value": value}
    conn.execute(
        """
        INSERT OR REPLACE INTO score_features(concept_id, feature, value, payload_json)
        VALUES (?, ?, ?, ?)
        """,
        (concept_id, feature, float(value), json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


def insert_edge(
    conn: sqlite3.Connection,
    source_id: str,
    edge_type: str,
    target_id: str,
    weight: float,
    payload: dict[str, Any],
) -> None:
    edge_id = "edge:" + hashlib.sha256(f"{source_id}|{edge_type}|{target_id}".encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT OR REPLACE INTO graph_edges(edge_id, source_id, edge_type, target_id, weight, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (edge_id, source_id, edge_type, target_id, weight, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


def insert_shared_tag_edges(conn: sqlite3.Connection, concepts: list[dict[str, Any]]) -> None:
    by_tag: dict[str, list[str]] = {}
    for concept in concepts:
        for tag in concept.get("tags") or []:
            by_tag.setdefault(str(tag).lower(), []).append(concept["concept_id"])
    for tag, concept_ids in by_tag.items():
        if len(concept_ids) < 2:
            continue
        for source_id in concept_ids:
            for target_id in concept_ids:
                if source_id != target_id:
                    insert_edge(conn, source_id, "shares_tag", target_id, 0.12, {"tag": tag})


def extract_claims(concept: dict[str, Any]) -> list[str]:
    body = str(concept.get("body") or "")
    claims: list[str] = []
    in_claims = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            in_claims = line.lower().startswith("## claims")
            continue
        if in_claims and line.startswith("- "):
            claim = line[2:].strip()
            if claim:
                claims.append(claim)
    if concept.get("concept_type") == "claim" and concept.get("description"):
        claims.append(str(concept["description"]))
    return list(dict.fromkeys(claims))[:20]


def score_features_for_concept(concept: dict[str, Any]) -> dict[str, float]:
    return {
        "confidence": float(concept.get("confidence") or 0.0),
        "token_estimate": float(concept.get("token_estimate") or 0.0),
        "citation_count": float(concept.get("citation_count") or 0.0),
        "alias_count": float(len(concept.get("aliases") or [])),
        "tag_count": float(len(concept.get("tags") or [])),
        "source_hash_count": float(len(concept.get("source_hashes") or [])),
    }


def freshness_warning_for_concept(concept: dict[str, Any]) -> str:
    if concept.get("source_status") == "missing":
        return "source_missing"
    freshness = concept.get("freshness") if isinstance(concept.get("freshness"), dict) else {}
    status = str(freshness.get("status") or "")
    if status in {"stale", "needs_review"}:
        return status
    checked_at = str(freshness.get("checked_at") or "")
    if not checked_at:
        return "freshness_unknown"
    try:
        checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return "freshness_parse_failed"
    age_days = (datetime.now().astimezone() - checked.astimezone()).days
    if age_days > 180:
        return f"stale_{age_days}_days"
    return ""


def export_graph_edges_jsonl(db_path: Path, edges_path: Path) -> Path:
    ensure_dir(edges_path.parent)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT edge_id, source_id, edge_type, target_id, weight, payload_json
            FROM graph_edges
            ORDER BY source_id, edge_type, target_id
            """
        ).fetchall()
    finally:
        conn.close()
    records = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {}
        records.append(
            {
                "edge_id": row["edge_id"],
                "source_id": row["source_id"],
                "edge_type": row["edge_type"],
                "target_id": row["target_id"],
                "weight": row["weight"],
                "payload": payload,
            }
        )
    write_jsonl(edges_path, records)
    return edges_path


def load_indexed_concepts(out_root: Path) -> list[dict[str, Any]]:
    db_path = vault_index_path_for(out_root)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT payload_json, embedding_json FROM concepts ORDER BY concept_id").fetchall()
    finally:
        conn.close()
    concepts = []
    for row in rows:
        concept = json.loads(row["payload_json"])
        concept["embedding_json"] = row["embedding_json"]
        concepts.append(concept)
    return concepts


def rank_vault_concepts(
    concepts: list[dict[str, Any]],
    goal: str,
    *,
    feedback_model: dict[str, Any],
    fts_scores: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    terms = vault_terms(goal)
    resume_intent = has_resume_intent(goal)
    vector_scores = vector_scores_for(concepts, goal)
    failure_penalties = failure_penalties_for(concepts, terms)
    fallback_fts_scores = fts_like_scores(concepts, terms)
    fts_scores = {
        concept["concept_id"]: max((fts_scores or {}).get(concept["concept_id"], 0.0), fallback_fts_scores.get(concept["concept_id"], 0.0))
        for concept in concepts
    }
    query_family = query_family_for_text(goal)
    ranked = []
    for concept in concepts:
        candidate = {
            "path": concept["path"],
            "relative_path": concept["relative_path"],
            "source_group": "vault",
            "provider": "vault_index",
            "project_id": concept["concept_id"],
            "project_path": concept.get("source_path"),
            "project_name": concept.get("title"),
            "title": concept.get("title"),
        }
        feedback_parts = feedback_boost_parts(feedback_model, candidate, query_family=query_family)
        score_parts = {
            "catalog": catalog_prior(concept, resume_intent=resume_intent),
            "portfolio_guard": portfolio_guard(concept, resume_intent=resume_intent),
            "exact": exact_score(concept, terms),
            "fts": fts_scores.get(concept["concept_id"], 0.0),
            "vector": vector_scores.get(concept["concept_id"], 0.0),
            "graph": graph_prior(concept, terms, resume_intent=resume_intent),
            "freshness": freshness_score(concept),
            "failure": failure_penalties.get(concept["concept_id"], 0.0),
            "feedback": feedback_parts["total"],
        }
        score = round(sum(score_parts.values()), 6)
        enriched = dict(concept)
        enriched["score"] = score
        enriched["score_parts"] = {key: round(value, 6) for key, value in score_parts.items()}
        enriched["freshness_warning"] = freshness_warning_for_concept(concept)
        enriched["why_hit"] = hit_reasons(enriched, terms, resume_intent=resume_intent)
        ranked.append(enriched)
    ranked.sort(key=lambda item: (-item["score"], -resume_sort_priority(item), item["title"]))
    return ranked


def search_vault_fts(out_root: Path, terms: list[str], *, limit: int) -> dict[str, float]:
    db_path = vault_index_path_for(out_root)
    query = fts_query_for_terms(terms)
    if not query:
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT concept_id, rank
            FROM concepts_fts
            WHERE concepts_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    if not rows:
        return {}
    total = len(rows)
    return {str(row["concept_id"]): max(0.0, 0.5 - (index / max(1, total)) * 0.25) for index, row in enumerate(rows)}


def fts_query_for_terms(terms: list[str]) -> str:
    clauses = []
    for term in terms[:16]:
        cleaned = re.sub(r'["*()\s]+', " ", term).strip()
        if len(cleaned) < 2:
            continue
        clauses.append(f'"{cleaned}"')
    return " OR ".join(clauses)


def vector_scores_for(concepts: list[dict[str, Any]], goal: str) -> dict[str, float]:
    backend = get_embedding_backend(default_retrieval_config())
    rows = {
        concept["concept_id"]: {
            "path": concept.get("path"),
            "relative_path": concept.get("relative_path"),
            "text": embedding_text_for(concept),
            "embedding_json": concept.get("embedding_json") or "[]",
        }
        for concept in concepts
    }
    try:
        return backend.score_rows(rows, goal, limit=max(1, len(rows)))
    except RuntimeError:
        return {}


def fts_like_scores(concepts: list[dict[str, Any]], terms: list[str]) -> dict[str, float]:
    scores = {}
    for concept in concepts:
        haystack = searchable_text(concept).lower()
        hits = sum(1 for term in terms if term.lower() in haystack)
        if hits:
            scores[concept["concept_id"]] = min(0.5, hits * 0.08)
    return scores


def catalog_prior(concept: dict[str, Any], *, resume_intent: bool) -> float:
    if resume_intent:
        return PRIMARY_RESUME_PRIOR.get(concept["concept_id"], 0.06 if concept.get("concept_type") == "project" else 0.0)
    return 0.08 if concept.get("concept_type") == "project" else 0.03


def portfolio_guard(concept: dict[str, Any], *, resume_intent: bool) -> float:
    if not resume_intent:
        return 0.0
    concept_id = concept.get("concept_id")
    if concept_id in PORTFOLIO_RESUME_GUARD:
        return PORTFOLIO_RESUME_GUARD[concept_id]
    if concept_id == "project-doctor":
        return -0.2
    return 0.0


def exact_score(concept: dict[str, Any], terms: list[str]) -> float:
    haystack = searchable_text(concept).lower()
    title_alias_text = " ".join([concept.get("title", ""), *concept.get("aliases", []), *concept.get("tags", [])]).lower()
    weighted = 0.0
    for term in terms:
        lower = term.lower()
        if lower in title_alias_text:
            weighted += 0.16
        elif lower in haystack:
            weighted += 0.06
    return min(0.8, weighted)


def graph_prior(concept: dict[str, Any], terms: list[str], *, resume_intent: bool) -> float:
    tags = {str(tag).lower() for tag in concept.get("tags") or []}
    aliases = {str(alias).lower() for alias in concept.get("aliases") or []}
    score = 0.0
    if resume_intent and "resume" in tags:
        score += 0.22
    if resume_intent and "project" in tags:
        score += 0.12
    for term in terms:
        lower = term.lower()
        if lower in tags or lower in aliases:
            score += 0.08
    return min(0.5, score)


def freshness_score(concept: dict[str, Any]) -> float:
    warning = freshness_warning_for_concept(concept)
    if not warning:
        return 0.0
    if warning == "source_missing":
        return -0.18
    if warning.startswith("stale") or warning == "needs_review":
        return -0.12
    return -0.06


def failure_penalties_for(concepts: list[dict[str, Any]], terms: list[str]) -> dict[str, float]:
    failures = [concept for concept in concepts if concept.get("concept_type") == "failure"]
    penalties: dict[str, float] = {}
    if not failures:
        return penalties
    for failure in failures:
        failure_text = searchable_text(failure).lower()
        term_hits = sum(1 for term in terms if term.lower() in failure_text)
        if term_hits == 0:
            continue
        for concept in concepts:
            if concept.get("concept_type") == "failure":
                continue
            identifiers = [
                concept.get("concept_id"),
                concept.get("title"),
                concept.get("source_path"),
                *(concept.get("aliases") or []),
            ]
            if any(identifier and str(identifier).lower() in failure_text for identifier in identifiers):
                penalties[concept["concept_id"]] = penalties.get(concept["concept_id"], 0.0) - min(0.3, 0.08 * term_hits)
    return penalties


def hit_reasons(concept: dict[str, Any], terms: list[str], *, resume_intent: bool) -> list[str]:
    reasons = []
    tags = {str(tag).lower() for tag in concept.get("tags") or []}
    aliases = {str(alias).lower() for alias in concept.get("aliases") or []}
    if resume_intent and "resume" in tags:
        reasons.append("任务含求职/简历意图，concept 带 `resume` 标签。")
    if resume_intent and concept["concept_id"] in PORTFOLIO_PROJECT_IDS:
        reasons.append("这是用户明确标记的主作品集项目，求职场景优先保留。")
    if resume_intent and concept["concept_id"] == "project-doctor":
        reasons.append("Doctor 是基础设施项目，求职场景可用但不应压过主作品集项目。")
    if concept["concept_id"] in PRIMARY_RESUME_PRIOR and resume_intent:
        reasons.append("这是 baseline 中标记为用户主要投入的项目，适合作为简历候选。")
    warning = concept.get("freshness_warning") or freshness_warning_for_concept(concept)
    if warning:
        reasons.append(f"Freshness warning: {warning}。")
    if concept.get("score_parts", {}).get("failure", 0.0) < 0:
        reasons.append("存在失败路径记忆，当前任务下已降低排序。")
    matched_terms = [term for term in terms if term.lower() in searchable_text(concept).lower()]
    if matched_terms:
        reasons.append(f"命中关键词/别名：{', '.join(matched_terms[:8])}。")
    if aliases:
        reasons.append(f"可作为别名入口：{', '.join(sorted(aliases)[:5])}。")
    if not reasons:
        reasons.append("通过目录/catalog prior 纳入候选，但缺少强关键词证据。")
    return reasons


def missed_concepts(ranked: list[dict[str, Any]], *, selected_ids: set[str], limit: int) -> list[dict[str, Any]]:
    misses = []
    for item in ranked:
        if item["concept_id"] in selected_ids:
            continue
        reasons = []
        if item["score_parts"].get("exact", 0.0) <= 0:
            reasons.append("没有明显关键词/别名命中。")
        if item["score_parts"].get("graph", 0.0) <= 0:
            reasons.append("没有匹配到当前任务需要的标签或图谱边。")
        if item["score_parts"].get("feedback", 0.0) < 0:
            reasons.append("历史反馈降低了排序。")
        if item["score_parts"].get("failure", 0.0) < 0:
            reasons.append("失败路径记忆降低了排序。")
        if item.get("freshness_warning"):
            reasons.append(f"存在 freshness warning: {item['freshness_warning']}。")
        if not reasons:
            reasons.append(f"被 limit={limit} 截断，分数低于已选概念。")
        misses.append(
            {
                "concept_id": item["concept_id"],
                "title": item["title"],
                "score": item["score"],
                "reasons": reasons,
            }
        )
    if not misses:
        misses.append(
            {
                "concept_id": "none",
                "title": "No excluded canonical concepts",
                "score": 0.0,
                "reasons": ["当前 limit 覆盖了全部 canonical concept，没有被排除的 concept。"],
            }
        )
    return misses


def estimate_vault_tokens(out_root: Path, concepts: list[dict[str, Any]]) -> dict[str, Any]:
    vault = out_root / "vault"
    vault_files = [path for path in vault.rglob("*.md") if path.is_file()]
    all_vault_chars = sum(len(path.read_text(encoding="utf-8", errors="replace")) for path in vault_files)
    canonical_tokens = sum(int(concept.get("token_estimate") or 0) for concept in concepts)
    source_projection = estimate_source_projection(concepts)
    return {
        "estimator": "ceil(chars/3)",
        "canonical_concepts": len(concepts),
        "vault_markdown_files": len(vault_files),
        "current_vault_chars": all_vault_chars,
        "current_vault_token_estimate": estimate_tokens_from_chars(all_vault_chars),
        "canonical_concept_token_estimate": canonical_tokens,
        "fits_128k_context": estimate_tokens_from_chars(all_vault_chars) < 128_000,
        "fits_200k_context": estimate_tokens_from_chars(all_vault_chars) < 200_000,
        "expanded_source_projection": source_projection,
        "why_not_feed_all": (
            "当前 baseline vault 很小，通常可全喂；但一旦把 source_path 下的证据文件全部编译进 vault，"
            "token 会按源文件规模增长，应使用 resolver 按需激活。"
        ),
    }


def estimate_source_projection(
    concepts: list[dict[str, Any]],
    *,
    max_files: int = 20_000,
    max_bytes: int = 40_000_000,
    max_bytes_per_root: int = 8_000_000,
) -> dict[str, Any]:
    files = 0
    bytes_seen = 0
    truncated = False
    roots = []
    for concept in concepts:
        source_path = Path(str(concept.get("source_path") or ""))
        if source_path.exists() and source_path.is_dir():
            roots.append(str(source_path))
            root_bytes = 0
            for path in source_path.rglob("*"):
                if should_skip_source_path(path):
                    continue
                if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                files += 1
                bytes_seen += size
                root_bytes += size
                if root_bytes >= max_bytes_per_root:
                    truncated = True
                    break
                if files >= max_files or bytes_seen >= max_bytes:
                    truncated = True
                    break
        if truncated:
            if files >= max_files or bytes_seen >= max_bytes:
                break
    return {
        "source_roots_sampled": roots,
        "text_files_sampled": files,
        "text_bytes_sampled": bytes_seen,
        "token_estimate": estimate_tokens_from_chars(bytes_seen),
        "truncated": truncated,
        "budget": {"max_files": max_files, "max_bytes": max_bytes, "max_bytes_per_root": max_bytes_per_root},
    }


def source_for_candidate(candidate: dict[str, Any], goal: str) -> dict[str, Any]:
    return {
        "type": "vault_concept",
        "source_type": "project" if candidate.get("concept_type") == "project" else "document",
        "source_group": "vault",
        "source_id": "vault",
        "source_chunk_id": f"vault:{candidate['concept_id']}",
        "provider": "vault_index",
        "score": normalized_vault_score(candidate["score"]),
        "score_parts": {**candidate["score_parts"], "vault_raw": candidate["score"]},
        "concept_id": candidate["concept_id"],
        "project_id": candidate["concept_id"],
        "title": candidate["title"],
        "path": candidate["path"],
        "relative_path": candidate["relative_path"],
        "project_path": candidate.get("source_path"),
        "project_name": candidate.get("title"),
        "summary": candidate["description"],
        "citations": candidate.get("citations") or [],
        "freshness": candidate.get("freshness") or {},
        "freshness_warning": candidate.get("freshness_warning") or freshness_warning_for_concept(candidate),
        "confidence": candidate.get("confidence", 0.0),
        "snippet": snippet(candidate.get("body") or candidate["text"], 420),
        "retrieval_query": goal,
        "matched_queries": vault_terms(goal),
        "entities": candidate.get("aliases") or [],
        "edges": [
            {"type": "has_tag", "target": tag, "weight": 0.35}
            for tag in candidate.get("tags") or []
        ],
    }


def normalized_vault_score(score: float) -> float:
    return round(max(0.0, min(1.0, float(score or 0.0) / 5.0)), 6)


def write_vault_check_report(out_root: Path, issues: list[dict[str, Any]], summary: dict[str, Any], *, rebuild: bool) -> Path:
    report_path = out_root / "reports" / "vault_check_report.md"
    status = "error" if any(issue["severity"] == "error" for issue in issues) else "warning" if issues else "ok"
    lines = [
        "# Vault Check Report",
        "",
        f"- Status: `{status}`",
        f"- Rebuild requested: `{rebuild}`",
        f"- Vault: `{summary.get('vault', out_root / 'vault')}`",
        f"- Index: `{summary.get('index_path', vault_index_path_for(out_root))}`",
        f"- OKF version: `{summary.get('okf_version', OKF_VERSION)}`",
        f"- OKF markdown files: `{summary.get('okf_markdown_files', 0)}`",
        f"- Markdown concepts: `{summary.get('markdown_concepts', 0)}`",
        f"- Indexed concepts: `{summary.get('indexed_concepts', 0)}`",
        "",
        "## OKF Required Frontmatter",
        "",
        ", ".join(f"`{field}`" for field in OKF_REQUIRED_FRONTMATTER_FIELDS),
        "",
        "## OKF Recommended Frontmatter",
        "",
        ", ".join(f"`{field}`" for field in OKF_RECOMMENDED_FRONTMATTER_FIELDS),
        "",
        "## Doctor Extension Frontmatter",
        "",
        ", ".join(f"`{field}`" for field in DOCTOR_REQUIRED_FRONTMATTER_FIELDS),
        "",
        "## Issues",
        "",
    ]
    if not issues:
        lines.append("- No issues found.")
    for issue in issues:
        detail = json.dumps(issue, ensure_ascii=False, sort_keys=True)
        lines.append(f"- `{issue['severity']}` {issue['message']} — `{issue.get('path', '')}`")
        lines.append(f"  - `{detail}`")
    write_text(report_path, "\n".join(lines) + "\n")
    return report_path


def rank_existing_context_packs(out_root: Path, goal: str, *, limit: int) -> list[dict[str, Any]]:
    terms = [term.lower() for term in vault_terms(goal) if len(term) >= 2]
    packs_root = out_root / "packs"
    if not packs_root.exists():
        return []
    candidates = []
    for context_path in sorted(packs_root.glob("*/context.md"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)[:300]:
        try:
            text = context_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        haystack = f"{context_path}\n{text}".lower()
        hits = [term for term in terms if term in haystack]
        if not hits:
            continue
        candidates.append(
            {
                "context_md_path": str(context_path),
                "score": round(min(1.0, len(hits) / max(1, len(terms))), 6),
                "matched_terms": hits[:12],
                "token_estimate": estimate_tokens(text),
            }
        )
    candidates.sort(key=lambda item: (-float(item["score"]), item["context_md_path"]))
    return candidates[:limit]


def render_wiki_baseline_eval_report(payload: dict[str, Any]) -> str:
    lines = [
        "# LLM-Wiki / OKF Baseline Evaluation",
        "",
        f"- Created: `{payload['created_at']}`",
        f"- Cases: {len(payload['cases'])}",
        f"- Current vault token estimate: {payload['token_report']['current_vault_token_estimate']}",
        f"- Canonical concept token estimate: {payload['token_report']['canonical_concept_token_estimate']}",
        "",
        "## Cases",
        "",
    ]
    for case in payload["cases"]:
        lines.append(f"### {case['goal']}")
        lines.append("")
        provider_scores = case["raw_file_search"].get("provider_scores") or {}
        lines.append("#### Raw File Search")
        lines.append("")
        if provider_scores:
            for source_id, stats in provider_scores.items():
                lines.append(f"- `{source_id}` score={stats.get('score')} hits={stats.get('hits')} files={stats.get('unique_files')}")
        else:
            lines.append("- No strong raw grep provider match.")
        lines.append("")
        lines.append("#### Existing Context Packs")
        lines.append("")
        if case["existing_context_packs"]:
            for pack in case["existing_context_packs"]:
                lines.append(f"- `{pack['context_md_path']}` score={pack['score']} tokens~{pack['token_estimate']}")
        else:
            lines.append("- No existing context pack matched this task strongly.")
        lines.append("")
        lines.append("#### Vault Retrieval")
        lines.append("")
        for concept in case["vault_retrieval"]["top_concepts"]:
            lines.append(
                f"- `{concept['concept_id']}` {concept['title']} score={concept['score']} "
                f"parts={json.dumps(concept['score_parts'], ensure_ascii=False, sort_keys=True)}"
            )
        lines.append("")
        lines.append("#### Full Vault Context")
        lines.append("")
        full_vault = case["full_vault_context"]
        lines.append(f"- Current vault tokens: {full_vault['current_vault_token_estimate']}")
        lines.append(f"- Fits 128k: {full_vault['fits_128k_context']}")
        lines.append(f"- Fits 200k: {full_vault['fits_200k_context']}")
        lines.append(f"- Judgment: {full_vault['judgment']}")
        lines.append("")
    return "\n".join(lines)


def render_vault_context(
    goal: str,
    created_at: str,
    selected: list[dict[str, Any]],
    misses: list[dict[str, Any]],
    token_report: dict[str, Any],
    state_path: Path,
) -> str:
    lines = [
        "---",
        f"vault_resolver_version: {VAULT_RESOLVER_VERSION}",
        f"goal: {goal}",
        f"created_at: {created_at}",
        "---",
        "",
        "# Task",
        "",
        goal,
        "",
        "# Fast Answer Context",
        "",
        "Doctor searched the LLM-Wiki / OKF Vault instead of feeding the whole vault to the model.",
        "",
        "## Selected Concepts",
        "",
    ]
    for index, item in enumerate(selected, start=1):
        lines.append(f"### {index}. {item['title']}")
        lines.append("")
        lines.append(f"- Concept ID: `{item['concept_id']}`")
        lines.append(f"- Source path: `{item.get('source_path')}`")
        lines.append(f"- Score: `{item['score']}`")
        lines.append(f"- Score parts: `{json.dumps(item['score_parts'], ensure_ascii=False, sort_keys=True)}`")
        if item.get("freshness_warning"):
            lines.append(f"- Freshness warning: `{item['freshness_warning']}`")
        lines.append(f"- Confidence: `{item.get('confidence', 0.0)}`")
        for reason in item.get("why_hit") or []:
            lines.append(f"- Why: {reason}")
        lines.append(f"- Summary: {snippet(item['description'], 360)}")
        lines.append("")
        lines.append("> " + snippet(item.get("body") or item["text"], 500))
        lines.append("")
    lines.extend(["## Non-Hits / Miss Reasons", ""])
    for miss in misses:
        lines.append(f"- `{miss['title']}` score={miss['score']}: {'; '.join(miss['reasons'])}")
    lines.extend(
        [
            "",
            "## Token Boundary",
            "",
            f"- Current vault Markdown token estimate: {token_report['current_vault_token_estimate']}",
            f"- Canonical concept token estimate: {token_report['canonical_concept_token_estimate']}",
            f"- Fits 128k context now: {token_report['fits_128k_context']}",
            f"- Expanded source projection tokens: {token_report['expanded_source_projection']['token_estimate']}",
            f"- Projection truncated: {token_report['expanded_source_projection']['truncated']}",
            f"- Boundary judgment: {token_report['why_not_feed_all']}",
            "",
            "## Slow Answer State",
            "",
            f"- Anytime state: `{state_path}`",
            "- If the user says this is wrong, continue from that state and expand the frontier.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_anytime_state(
    task_id: str,
    goal: str,
    mode: str,
    selected: list[dict[str, Any]],
    frontier: list[dict[str, Any]],
    token_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "goal": goal,
        "mode": mode,
        "status": "running" if mode in {"slow", "anytime"} else "fast_answer_ready",
        "policy": {
            "strategy": "epsilon_greedy_over_graph_frontier",
            "exploit": "continue from high score concepts and shared tags",
            "explore": "reserve candidates with weak exact match but useful aliases/tags",
        },
        "selected_concepts": [compact_candidate(item) for item in selected],
        "frontier": [compact_candidate(item) for item in frontier[:20]],
        "token_report": token_report,
        "next_action": "stop_if_user_satisfied_else_expand_frontier",
    }


def update_anytime_state(path: Path, *, feedback: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"anytime state not found: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    state["last_feedback"] = feedback
    state["updated_at"] = datetime.now().astimezone().isoformat()
    if feedback == "satisfied":
        state["status"] = "stopped_satisfied"
        state["next_action"] = "no_more_expansion"
    elif feedback == "not_right":
        state["status"] = "needs_expansion"
        state["next_action"] = "run vault-resolve again with a larger limit or source-specific query"
    write_text(path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"status": state["status"], "anytime_state_json_path": str(path), "state": state}


def rank_anytime_source_files(
    state: dict[str, Any],
    terms: list[str],
    *,
    limit: int,
    max_files_per_root: int,
) -> list[dict[str, Any]]:
    roots = concepts_for_source_expansion(state)
    candidates = []
    for root_index, concept in enumerate(roots):
        source_path = Path(str(concept.get("source_path") or ""))
        if not source_path.exists():
            continue
        for path in iter_source_files(source_path, max_files=max_files_per_root):
            candidates.append(source_file_candidate(path, concept, terms, root_index=root_index))
    candidates = [item for item in candidates if item]
    candidates.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            int(item.get("root_index") or 0),
            str(item.get("path") or ""),
        )
    )
    return diversify_source_candidates(candidates, limit=limit, per_project_cap=3)


def diversify_source_candidates(candidates: list[dict[str, Any]], *, limit: int, per_project_cap: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    selected_keys: set[str] = set()
    for candidate in candidates:
        project_id = str(candidate.get("project_id") or "")
        if counts.get(project_id, 0) >= per_project_cap:
            continue
        selected.append(candidate)
        selected_keys.add(str(candidate.get("path") or ""))
        counts[project_id] = counts.get(project_id, 0) + 1
        if len(selected) >= limit:
            return selected
    for candidate in candidates:
        key = str(candidate.get("path") or "")
        if key in selected_keys:
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def concepts_for_source_expansion(state: dict[str, Any]) -> list[dict[str, Any]]:
    selected = state.get("selected_concepts") if isinstance(state.get("selected_concepts"), list) else []
    frontier = state.get("frontier") if isinstance(state.get("frontier"), list) else []
    ordered = []
    seen = set()
    for item in [*selected, *frontier]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("concept_id") or item.get("source_path") or item.get("path") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def iter_source_files(source_path: Path, *, max_files: int) -> list[Path]:
    if source_path.is_file():
        return [source_path] if source_path.suffix.lower() in TEXT_SUFFIXES else []
    found: list[Path] = []
    for current_root, dirnames, filenames in os.walk(source_path):
        dirnames[:] = [
            dirname
            for dirname in sorted(dirnames)
            if dirname not in SKIP_SOURCE_DIRS and not dirname.startswith(".")
        ]
        root_path = Path(current_root)
        priority = sorted(filenames, key=lambda name: (name not in SOURCE_PRIORITY_NAMES, name.lower()))
        for filename in priority:
            path = root_path / filename
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            found.append(path)
            if len(found) >= max_files:
                return found
    return found


def source_file_candidate(path: Path, concept: dict[str, Any], terms: list[str], *, root_index: int) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {}
    text = read_text_preview(path)
    haystack = f"{path} {text}".lower()
    path_text = str(path).lower()
    exact_hits = [term for term in terms if term.lower() in haystack]
    path_hits = [term for term in terms if term.lower() in path_text]
    score_parts = {
        "exact": min(0.8, len(exact_hits) * 0.08),
        "path": min(0.5, len(path_hits) * 0.12),
        "priority_file": priority_file_score(path),
        "project_prior": max(0.0, float(concept.get("score") or 0.0)) * 0.08,
        "portfolio": source_portfolio_score(concept, terms),
        "exploration": 0.08 if root_index >= 4 else 0.0,
    }
    score = round(sum(score_parts.values()), 6)
    if score <= 0:
        score = round(0.01 + max(0.0, 4 - root_index) * 0.005, 6)
    return {
        "type": "vault_source_file",
        "source_type": "code" if path.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".swift"} else "document",
        "source_group": "vault",
        "provider": "vault_anytime",
        "project_id": concept.get("concept_id"),
        "project_path": concept.get("source_path"),
        "project_name": concept.get("title"),
        "title": f"{concept.get('title')} / {path.name}",
        "path": str(path.resolve()),
        "relative_path": relative_to_source(path, Path(str(concept.get("source_path") or ""))),
        "score": score,
        "score_parts": {key: round(value, 6) for key, value in score_parts.items()},
        "root_index": root_index,
        "size_bytes": stat.st_size,
        "sha256": sha256_file(path),
        "snippet": snippet(text, 520),
        "summary": f"Slow-answer expansion source from {concept.get('title')}: {path.name}",
        "matched_queries": exact_hits[:12],
        "retrieval_channel": ["anytime_source_expansion", "bandit_round_robin"],
    }


def priority_file_score(path: Path) -> float:
    name = path.name
    parts = {part.lower() for part in path.parts}
    if name in SOURCE_PRIORITY_NAMES:
        return 0.5
    if "docs" in parts or "doc" in parts:
        return 0.28
    if "src" in parts or "tests" in parts:
        return 0.18
    return 0.04


def source_portfolio_score(concept: dict[str, Any], terms: list[str]) -> float:
    if not any(term in {"resume", "简历", "求职", "作品", "作品集", "包装"} for term in terms):
        return 0.0
    concept_id = str(concept.get("concept_id") or "")
    if concept_id in PORTFOLIO_RESUME_GUARD:
        return PORTFOLIO_RESUME_GUARD[concept_id] * 0.35
    if concept_id == "project-doctor":
        return -0.35
    return 0.0


def read_text_preview(path: Path, *, max_chars: int = 12_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(max_chars)
    except OSError:
        return ""


def relative_to_source(path: Path, source_path: Path) -> str:
    try:
        return str(path.resolve().relative_to(source_path.resolve()))
    except (OSError, ValueError):
        return path.name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_anytime_step_context(
    goal: str,
    created_at: str,
    state: dict[str, Any],
    sources: list[dict[str, Any]],
    state_path: Path,
) -> str:
    lines = [
        "---",
        f"vault_resolver_version: {VAULT_RESOLVER_VERSION}",
        f"goal: {goal}",
        f"created_at: {created_at}",
        "mode: anytime_step",
        "---",
        "",
        "# Task",
        "",
        goal,
        "",
        "# Slow Answer Expansion",
        "",
        "The fast answer was not accepted or needs more evidence. Doctor expanded from selected Vault concepts into their source paths with a bounded file budget.",
        "",
        f"- Previous task id: `{state.get('task_id')}`",
        f"- State file: `{state_path}`",
        f"- Sources included: {len(sources)}",
        "",
        "## Expanded Sources",
        "",
    ]
    for index, source in enumerate(sources, start=1):
        lines.append(f"### {index}. {source.get('title')}")
        lines.append("")
        lines.append(f"- Project: `{source.get('project_id')}`")
        lines.append(f"- Path: `{source.get('path')}`")
        lines.append(f"- Score: `{source.get('score')}`")
        lines.append(f"- Score parts: `{json.dumps(source.get('score_parts') or {}, ensure_ascii=False, sort_keys=True)}`")
        lines.append(f"- SHA-256: `{source.get('sha256')}`")
        lines.append("")
        lines.append("> " + snippet(str(source.get("snippet") or ""), 600))
        lines.append("")
    if not sources:
        lines.append("- No source files were available inside the current expansion budget.")
        lines.append("")
    lines.extend(
        [
            "## Next Step",
            "",
            "- If this expanded context is useful, mark the state as satisfied.",
            "- If it is still wrong, run another anytime step with a larger file budget or a refined goal.",
        ]
    )
    return "\n".join(lines) + "\n"


def compact_source_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": item.get("path"),
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "score": item.get("score"),
        "score_parts": item.get("score_parts"),
        "sha256": item.get("sha256"),
    }


def compact_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "concept_id": item["concept_id"],
        "title": item["title"],
        "score": item.get("score"),
        "score_parts": item.get("score_parts"),
        "path": item.get("path"),
        "source_path": item.get("source_path"),
    }


def write_vault_index_report(
    out_root: Path,
    concepts: list[dict[str, Any]],
    db_path: Path,
    fts_enabled: bool,
    *,
    knowledge_path: Path,
    edges_path: Path,
) -> Path:
    report_path = out_root / "reports" / "vault_index_report.md"
    total_tokens = sum(int(concept["token_estimate"]) for concept in concepts)
    lines = [
        "# Vault Index Report",
        "",
        f"- Index path: `{db_path}`",
        f"- Knowledge index path: `{knowledge_path}`",
        f"- Knowledge edges JSONL: `{edges_path}`",
        f"- Concepts indexed: {len(concepts)}",
        f"- FTS enabled: {fts_enabled}",
        f"- Canonical concept token estimate: {total_tokens}",
        "",
        "## Concepts",
        "",
    ]
    for concept in concepts:
        lines.append(f"- `{concept['concept_id']}` {concept['title']} from `{concept.get('source_path')}`")
    write_text(report_path, "\n".join(lines) + "\n")
    return report_path


def reset_sqlite_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines()
    end_index = 0
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            end_index = index
            break
    if not end_index:
        return {}, text
    raw_frontmatter = "\n".join(lines[1:end_index])
    frontmatter: dict[str, Any] = {}
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw_frontmatter) or {}
            if isinstance(parsed, dict):
                frontmatter = dict(parsed)
        except Exception:
            frontmatter = {}
    if not frontmatter:
        for line in lines[1:end_index]:
            if ": " not in line:
                continue
            key, raw = line.split(": ", 1)
            try:
                frontmatter[key] = json.loads(raw)
            except json.JSONDecodeError:
                frontmatter[key] = raw.strip().strip('"')
    body = "\n".join(lines[end_index + 1 :])
    return frontmatter, body


def list_field(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        compact = " ".join(block.strip().split())
        if compact and not compact.startswith("#"):
            return compact
    return ""


def embedding_text_for(concept: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(concept.get("title") or ""),
            str(concept.get("description") or ""),
            " ".join(concept.get("aliases") or []),
            " ".join(concept.get("tags") or []),
            str(concept.get("source_path") or ""),
            str(concept.get("text") or "")[:6000],
        ]
    )


def searchable_text(concept: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(concept.get("title") or ""),
            str(concept.get("description") or ""),
            " ".join(concept.get("aliases") or []),
            " ".join(concept.get("tags") or []),
            str(concept.get("source_path") or ""),
            str(concept.get("text") or ""),
        ]
    )


def vault_terms(goal: str) -> list[str]:
    terms = query_terms(goal)
    lower = goal.lower()
    if has_resume_intent(goal):
        terms.extend(["resume", "project", "项目", "作品", "求职", "简历", "包装", "产品", "工程", "长期投入"])
    if "个人" in lower:
        terms.extend(["personal", "long-term", "长期", "用户"])
    deduped = []
    seen = set()
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def has_resume_intent(goal: str) -> bool:
    lower = goal.lower()
    return any(marker in lower for marker in RESUME_INTENT_MARKERS)


def resume_sort_priority(item: dict[str, Any]) -> float:
    return PRIMARY_RESUME_PRIOR.get(item.get("concept_id"), 0.0)


def estimate_tokens(text: str) -> int:
    return estimate_tokens_from_chars(len(text))


def estimate_tokens_from_chars(chars: int) -> int:
    return int(math.ceil(chars / 3))


def should_skip_source_path(path: Path) -> bool:
    return any(part in SKIP_SOURCE_DIRS for part in path.parts)
