from __future__ import annotations

import hashlib
import json
import random
import shutil
from datetime import datetime
from pathlib import Path

from .compare import build_graph_context_pack
from .feedback_model import query_family_for_text, write_feedback_model
from .ingest import ingest_scope
from .io import ensure_dir, read_jsonl, write_jsonl, write_text
from .pack import build_context_pack, metadata_snippet, slugify, snippet, terms_for


ARENA_VERSION = "0.1"
EXPLORE_SOURCE_LIMIT = 20
RETRIEVAL_EVAL_CASES_FILENAME = "retrieval_eval_cases.jsonl"
MAX_RETRIEVAL_EVAL_EXPECTED_SOURCES = 64


def build_arena(scope: Path, out_root: Path, goal: str, *, skip_ingest: bool = False) -> dict:
    scope = scope.expanduser().resolve()
    out_root = out_root.expanduser().resolve()

    ingest_result = None
    if not skip_ingest:
        ingest_result = ingest_scope(scope, out_root)

    created_at = datetime.now().astimezone().isoformat()
    arena_id = f"{slugify(goal)}-arena-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    arena_dir = ensure_dir(out_root / "packs" / arena_id)

    route_a = build_context_pack(scope, out_root, goal)
    route_b = build_graph_context_pack(scope, out_root, goal)
    route_c = build_explore_context_pack(scope, out_root, goal)

    route_specs = [
        {
            "route": "a_chunk_pack",
            "hypothesis": "Direct evidence: extracted chunks and source quotes answer the task fastest.",
            "result": route_a,
        },
        {
            "route": "b_graph_context_map",
            "hypothesis": "Memory graph: source relationships identify better long-term memory candidates.",
            "result": route_b,
        },
        {
            "route": "c_explore_diversity",
            "hypothesis": "Exploration: diverse files, failures, and metadata-only sources catch what direct ranking misses.",
            "result": route_c,
        },
    ]

    rng = random.Random(hashlib.sha256(f"{goal}|{created_at}".encode("utf-8")).hexdigest())
    shuffled = list(route_specs)
    rng.shuffle(shuffled)

    candidates = []
    for index, spec in enumerate(shuffled, start=1):
        candidate_dir = ensure_dir(arena_dir / f"candidate-{index}")
        candidate = materialize_candidate(candidate_dir, index, spec, goal)
        candidates.append(candidate)

    slate = {
        "arena_version": ARENA_VERSION,
        "arena_id": arena_id,
        "goal": goal,
        "scope": str(scope),
        "created_at": created_at,
        "slate_md_path": str(arena_dir / "slate.md"),
        "slate_json_path": str(arena_dir / "slate.json"),
        "feedback_jsonl_path": str(arena_dir / "feedback.jsonl"),
        "candidates": candidates,
    }

    key = {
        "arena_id": arena_id,
        "goal": goal,
        "created_at": created_at,
        "candidate_routes": [
            {
                "candidate_id": candidate["candidate_id"],
                "route": candidate["route"],
                "hypothesis": candidate["hypothesis"],
            }
            for candidate in candidates
        ],
    }

    write_text(arena_dir / "slate.json", json.dumps(slate, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(arena_dir / "slate_key.json", json.dumps(key, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(arena_dir / "slate.md", render_slate_md(goal, scope, candidates))

    result = {
        "arena_id": arena_id,
        "goal": goal,
        "scope": str(scope),
        "slate_md_path": str(arena_dir / "slate.md"),
        "slate_json_path": str(arena_dir / "slate.json"),
        "slate_key_json_path": str(arena_dir / "slate_key.json"),
        "feedback_jsonl_path": str(arena_dir / "feedback.jsonl"),
        "candidates": [
            {
                "candidate_id": candidate["candidate_id"],
                "answer_md_path": candidate["answer_md_path"],
                "context_md_path": candidate["context_md_path"],
                "sources_jsonl_path": candidate["sources_jsonl_path"],
            }
            for candidate in candidates
        ],
    }
    if ingest_result is not None:
        result["ingest"] = ingest_result
    return result


def materialize_candidate(candidate_dir: Path, candidate_index: int, spec: dict, goal: str) -> dict:
    result = spec["result"]
    context_path = candidate_dir / "context.md"
    sources_path = candidate_dir / "sources.jsonl"
    manifest_path = candidate_dir / "route.json"
    answer_path = candidate_dir / "answer.md"

    shutil.copy2(result["context_md_path"], context_path)
    shutil.copy2(result["sources_jsonl_path"], sources_path)
    if result.get("manifest_json_path"):
        shutil.copy2(result["manifest_json_path"], candidate_dir / "manifest.json")
    if result.get("context_graph_path"):
        shutil.copy2(result["context_graph_path"], candidate_dir / "context_graph.json")

    sources = read_jsonl(sources_path)
    route_record = {
        "candidate_id": f"candidate-{candidate_index}",
        "route": spec["route"],
        "hypothesis": spec["hypothesis"],
        "source_pack": result,
        "sources_included": len(sources),
    }
    write_text(manifest_path, json.dumps(route_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(answer_path, render_candidate_answer(goal, spec["route"], sources, spec["hypothesis"]))

    return {
        "candidate_id": f"candidate-{candidate_index}",
        "route": spec["route"],
        "hypothesis": spec["hypothesis"],
        "answer_md_path": str(answer_path),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "route_json_path": str(manifest_path),
        "sources_included": len(sources),
    }


def build_explore_context_pack(scope: Path, out_root: Path, goal: str) -> dict:
    scope = scope.expanduser().resolve()
    out_root = out_root.expanduser().resolve()
    manifests = out_root / "manifests"
    packs = ensure_dir(out_root / "packs")

    documents = read_jsonl(manifests / "documents.jsonl")
    chunks = read_jsonl(manifests / "chunks.jsonl")
    failures = read_jsonl(manifests / "failures.jsonl")
    chunks_by_doc = group_chunks(chunks)
    selected_docs = rank_explore_documents(documents, chunks_by_doc, failures, goal)[:EXPLORE_SOURCE_LIMIT]

    created_at = datetime.now().astimezone().isoformat()
    task_id = f"{slugify(goal)}-route-c-explore-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    pack_dir = ensure_dir(packs / task_id)
    context_path = pack_dir / "context.md"
    sources_path = pack_dir / "sources.jsonl"
    manifest_path = pack_dir / "manifest.json"

    sources = [render_explore_source(doc, score, reason, chunks_by_doc) for score, reason, doc in selected_docs]
    manifest = {
        "context_pack_version": "0.1",
        "route": "c_explore_diversity",
        "task_id": task_id,
        "goal": goal,
        "scope": str(scope),
        "created_at": created_at,
        "documents_considered": len(documents),
        "sources_included": len(sources),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
    }

    write_jsonl(sources_path, sources)
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(context_path, render_explore_context(goal, scope, created_at, documents, failures, sources))

    return {
        "task_id": task_id,
        "route": "c_explore_diversity",
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "sources_included": len(sources),
    }


def group_chunks(chunks: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk["doc_id"], []).append(chunk)
    for doc_chunks in grouped.values():
        doc_chunks.sort(key=lambda item: item["chunk_index"])
    return grouped


def rank_explore_documents(
    documents: list[dict],
    chunks_by_doc: dict[str, list[dict]],
    failures: list[dict],
    goal: str,
) -> list[tuple[float, str, dict]]:
    terms = set(terms_for(goal))
    failed_paths = {failure.get("path") for failure in failures}
    extension_seen: set[str] = set()
    folder_seen: set[str] = set()
    ranked: list[tuple[float, str, dict]] = []

    for doc in sorted(documents, key=lambda item: item.get("relative_path") or ""):
        extension = doc.get("extension") or "[none]"
        folder = folder_for(doc)
        doc_chunks = chunks_by_doc.get(doc["doc_id"], [])
        haystack = f"{doc.get('relative_path', '')}\n{extension}\n".lower()
        if doc_chunks:
            haystack += doc_chunks[0].get("text", "").lower()
        overlap = sum(1 for term in terms if term in haystack)

        reason_parts = []
        score = 0.0
        if extension not in extension_seen:
            score += 1.4
            reason_parts.append(f"new extension {extension}")
            extension_seen.add(extension)
        if folder not in folder_seen:
            score += 1.1
            reason_parts.append(f"new folder {folder}")
            folder_seen.add(folder)
        if doc.get("policy") == "metadata_only":
            score += 0.9
            reason_parts.append("metadata-only visibility")
        if doc.get("path") in failed_paths or doc.get("status") == "failed":
            score += 1.2
            reason_parts.append("failed extraction signal")
        if doc_chunks:
            score += min(len(doc_chunks), 6) * 0.2
            reason_parts.append(f"{len(doc_chunks)} extracted chunks")
        if overlap:
            score += overlap * 0.8
            reason_parts.append(f"{overlap} goal-term overlaps")
        score += min(int(doc.get("size_bytes") or 0), 5_000_000) / 5_000_000 * 0.2

        if not reason_parts:
            reason_parts.append("low-ranked fallback")
        ranked.append((score, "; ".join(reason_parts), doc))

    ranked.sort(key=lambda item: (-item[0], item[2].get("relative_path") or ""))
    return ranked


def render_explore_source(doc: dict, score: float, reason: str, chunks_by_doc: dict[str, list[dict]]) -> dict:
    chunks = chunks_by_doc.get(doc["doc_id"], [])
    best_chunk = chunks[0] if chunks else None
    return {
        "type": "explore_document",
        "score": round(score, 4),
        "reason": reason,
        "doc_id": doc["doc_id"],
        "path": doc.get("path"),
        "relative_path": doc.get("relative_path"),
        "parser": doc.get("parser"),
        "status": doc.get("status"),
        "policy": doc.get("policy"),
        "extension": doc.get("extension"),
        "snippet": snippet(best_chunk["text"]) if best_chunk else metadata_snippet(doc),
    }


def render_explore_context(
    goal: str,
    scope: Path,
    created_at: str,
    documents: list[dict],
    failures: list[dict],
    sources: list[dict],
) -> str:
    lines = [
        "---",
        "context_pack_version: 0.1",
        "route: c_explore_diversity",
        f"goal: {goal}",
        f"scope: {scope}",
        f"created_at: {created_at}",
        "---",
        "",
        "# Task",
        "",
        goal,
        "",
        "# Route C Hypothesis",
        "",
        "Explore sources that direct ranking can miss: unusual folders, file types, metadata-only records, and extraction failures.",
        "",
        "# Exploration Summary",
        "",
        f"- Scope scanned: `{scope}`",
        f"- Documents considered: {len(documents)}",
        f"- Failure records: {len(failures)}",
        f"- Sources included: {len(sources)}",
        "",
        "# Diverse Candidates",
        "",
    ]
    for source in sources:
        lines.append(f"- `{source['path']}` score={source['score']} reason={source['reason']}")

    lines.extend(["", "# Evidence Snippets", ""])
    for source in sources[:10]:
        lines.append(f"- `{source.get('relative_path') or source.get('path')}`: {source['snippet']}")

    lines.extend(["", "# Limitations", ""])
    lines.append("- Route C is intentionally exploratory; it may include noisy records to surface blind spots.")
    lines.append("- Metadata-only and failed files need a follow-up parser, OCR, transcription, or manual inspection before promotion.")
    lines.append("- Use Route C to find missing clusters, not as the primary final answer.")
    lines.append("")
    return "\n".join(lines)


def render_candidate_answer(goal: str, route: str, sources: list[dict], hypothesis: str) -> str:
    recommendation = answer_recommendation(route)
    unique_sources = unique_sources_by_path(sources)
    lines = [
        "# Candidate Answer",
        "",
        "## Conclusion",
        "",
        recommendation,
        "",
        "## Why This Answer",
        "",
        f"- Goal: {goal}",
        f"- Sources reviewed: {len(sources)}",
        "",
        "## Recommended Files",
        "",
    ]

    if unique_sources:
        for source in unique_sources[:8]:
            path = source.get("path") or source.get("relative_path")
            reason = source.get("reason")
            terms = source.get("matched_terms") or []
            extra = f"; terms={', '.join(terms)}" if terms else ""
            if reason:
                extra += f"; reason={reason}"
            lines.append(f"- `{path}`{extra}")
    else:
        lines.append("- No sources were selected by this route.")

    lines.extend(["", "## Evidence", ""])
    for source in unique_sources[:5]:
        lines.append(f"- `{source.get('relative_path') or source.get('path')}`: {source.get('snippet', '')}")

    lines.extend(["", "## What To Do Next", ""])
    if route == "a_chunk_pack":
        lines.append("- Use this answer when you need direct snippets before deciding what to promote.")
    elif route == "b_graph_context_map":
        lines.append("- Use this answer when you want long-term memory candidates such as skills, workflows, decisions, and context docs.")
    else:
        lines.append("- Use this answer to inspect blind spots before trusting the higher-confidence routes.")
    lines.append("- Pick this candidate only if its recommended files match your intent better than the other candidates.")
    lines.append("")
    return "\n".join(lines)


def unique_sources_by_path(sources: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for source in sources:
        path = source.get("path") or source.get("relative_path") or source.get("doc_id")
        if path in seen:
            continue
        seen.add(path)
        unique.append(source)
    return unique


def answer_recommendation(route: str) -> str:
    if route == "a_chunk_pack":
        return "Prioritize files with direct textual evidence. This is the fastest route for Codex to continue the current task."
    if route == "b_graph_context_map":
        return "Prioritize agent assets and reusable context: skills, workflows, decisions, and memory-like documents."
    return "Prioritize exploration: inspect diverse or under-parsed sources that may reveal missing memory candidates."


def render_slate_md(goal: str, scope: Path, candidates: list[dict]) -> str:
    lines = [
        "# Arena Slate",
        "",
        f"Goal: {goal}",
        f"Scope: `{scope}`",
        "",
        "Read the three candidates and choose the best one. Candidate order is randomized; route labels are stored in `slate_key.json`.",
        "",
    ]
    for candidate in candidates:
        answer = Path(candidate["answer_md_path"]).read_text(encoding="utf-8")
        lines.extend(
            [
                f"## {candidate['candidate_id'].title()}",
                "",
                answer.strip(),
                "",
            ]
        )
    lines.extend(
        [
            "## Record Your Choice",
            "",
            "```bash",
            "agent-context feedback --slate <path-to-slate.json> --winner candidate-1 --reason \"best matches my intent\"",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def record_feedback(slate_path: Path, winner: str, reason: str = "") -> dict:
    slate_path = slate_path.expanduser().resolve()
    slate = json.loads(slate_path.read_text(encoding="utf-8"))
    candidates = slate.get("candidates", [])
    candidate_ids = {candidate["candidate_id"] for candidate in candidates}
    if winner not in candidate_ids:
        raise ValueError(f"winner must be one of {sorted(candidate_ids)}")

    created_at = datetime.now().astimezone().isoformat()
    winner_record = next(candidate for candidate in candidates if candidate["candidate_id"] == winner)
    winner_sources = feedback_source_keys(winner_record)
    candidate_feedback_records = []
    for candidate in candidates:
        source_keys = feedback_source_keys(candidate)
        candidate_feedback_records.append(
            {
                "candidate_id": candidate["candidate_id"],
                "route": candidate.get("route"),
                "selected": candidate["candidate_id"] == winner,
                "source_keys": source_keys,
                "source_count": len(source_keys),
            }
        )
    pairwise_comparisons = [
        {
            "winner": winner,
            "loser": candidate["candidate_id"],
            "winner_route": winner_record.get("route"),
            "loser_route": candidate.get("route"),
        }
        for candidate in candidates
        if candidate["candidate_id"] != winner
    ]
    feedback = {
        "arena_version": slate.get("arena_version"),
        "arena_id": slate.get("arena_id"),
        "goal": slate.get("goal"),
        "query_family": query_family_for_text(slate.get("goal")),
        "scope": slate.get("scope"),
        "winner": winner,
        "winner_route": winner_record.get("route"),
        "winner_sources": winner_sources,
        "reason": reason,
        "created_at": created_at,
        "pairwise_comparisons": pairwise_comparisons,
        "candidates": candidate_feedback_records,
    }

    arena_feedback_path = slate_path.parent / "feedback.jsonl"
    append_jsonl(arena_feedback_path, feedback)

    out_root = slate_path.parent.parent.parent
    global_feedback_path = out_root / "feedback" / "arena_feedback.jsonl"
    append_jsonl(global_feedback_path, feedback)
    feedback_model = write_feedback_model(out_root)
    retrieval_eval_case = write_arena_retrieval_eval_case(
        slate,
        winner_record,
        feedback,
        out_root=out_root,
        arena_dir=slate_path.parent,
    )

    return {
        "arena_feedback_path": str(arena_feedback_path),
        "global_feedback_path": str(global_feedback_path),
        "feedback_model_path": feedback_model["feedback_model_path"],
        "retrieval_eval_cases_path": retrieval_eval_case["global_path"],
        "retrieval_eval_cases_written": retrieval_eval_case["written"],
        "pairwise_comparisons": len(pairwise_comparisons),
        "winner": winner,
        "winner_route": winner_record.get("route"),
        "winner_sources": winner_sources,
    }


def feedback_source_keys(candidate: dict) -> list[str]:
    sources_path = candidate.get("sources_jsonl_path")
    if not sources_path:
        return []
    keys = []
    for source in read_jsonl(Path(sources_path)):
        for field in ("path", "relative_path", "source_id", "source_chunk_id", "doc_id"):
            value = source.get(field)
            if value:
                keys.append(str(value))
    return list(dict.fromkeys(keys))


def write_arena_retrieval_eval_case(
    slate: dict,
    winner_record: dict,
    feedback: dict,
    *,
    out_root: Path,
    arena_dir: Path,
) -> dict:
    case = arena_retrieval_eval_case(slate, winner_record, feedback)
    global_path = out_root / "feedback" / RETRIEVAL_EVAL_CASES_FILENAME
    arena_path = arena_dir / RETRIEVAL_EVAL_CASES_FILENAME
    if not case["expected_sources"]:
        return {"written": 0, "global_path": str(global_path), "arena_path": str(arena_path), "case": case}

    written = 0
    for path in (arena_path, global_path):
        if retrieval_eval_case_exists(path, case["origin_id"]):
            continue
        append_jsonl(path, case)
        written += 1
    return {"written": written, "global_path": str(global_path), "arena_path": str(arena_path), "case": case}


def arena_retrieval_eval_case(slate: dict, winner_record: dict, feedback: dict) -> dict:
    sources = read_jsonl(Path(winner_record["sources_jsonl_path"])) if winner_record.get("sources_jsonl_path") else []
    expected_sources = retrieval_eval_expected_sources(sources)
    arena_id = str(slate.get("arena_id") or "")
    winner = str(feedback.get("winner") or "")
    return {
        "query": str(slate.get("goal") or ""),
        "source": infer_retrieval_eval_source(slate, sources),
        "expected_sources": expected_sources,
        "notes": str(feedback.get("reason") or ""),
        "origin": "arena_feedback",
        "origin_id": f"arena:{arena_id}:{winner}",
        "arena_id": arena_id,
        "winner": winner,
        "winner_route": winner_record.get("route"),
        "query_family": feedback.get("query_family"),
        "created_at": feedback.get("created_at"),
    }


def retrieval_eval_expected_sources(sources: list[dict]) -> list[str]:
    values = []
    for source in unique_sources_by_path(sources):
        for field in ("path", "relative_path", "source_chunk_id", "source_id", "doc_id", "project_name"):
            value = source.get(field)
            if value:
                values.append(str(value))
    return list(dict.fromkeys(values))[:MAX_RETRIEVAL_EVAL_EXPECTED_SOURCES]


def infer_retrieval_eval_source(slate: dict, sources: list[dict]) -> str:
    source_markers = {
        str(source.get("source_group") or source.get("provider") or source.get("type") or "")
        for source in sources
    }
    if source_markers & {"git_repositories", "project_code_index", "project_code", "git_project"}:
        return "projects"
    if any("project" in marker for marker in source_markers):
        return "projects"
    scope = str(slate.get("scope") or "").lower()
    if "downloads" in scope or "下载" in scope:
        return "downloads"
    return "downloads"


def retrieval_eval_case_exists(path: Path, origin_id: str) -> bool:
    if not origin_id:
        return False
    return any(record.get("origin_id") == origin_id for record in read_jsonl(path))


def append_jsonl(path: Path, record: dict) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def folder_for(doc: dict) -> str:
    relative = Path(doc.get("relative_path") or doc.get("path") or "")
    parent = str(relative.parent)
    return "." if parent in {"", "."} else parent
