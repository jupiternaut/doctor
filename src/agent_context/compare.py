from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .ingest import ingest_scope
from .io import ensure_dir, read_jsonl, write_jsonl, write_text
from .pack import (
    MAX_SOURCES,
    build_context_pack,
    metadata_snippet,
    slugify,
    snippet,
    terms_for,
)


def compare_routes(scope: Path, out_root: Path, goal: str, *, skip_ingest: bool = False) -> dict:
    scope = scope.expanduser().resolve()
    out_root = out_root.expanduser().resolve()

    ingest_result = None
    if not skip_ingest:
        ingest_result = ingest_scope(scope, out_root)

    route_a = build_context_pack(scope, out_root, goal)
    route_b = build_graph_context_pack(scope, out_root, goal)
    report_path = write_comparison_report(out_root, goal, scope, route_a, route_b)

    result = {
        "goal": goal,
        "scope": str(scope),
        "route_a": route_a,
        "route_b": route_b,
        "comparison_report": str(report_path),
    }
    if ingest_result is not None:
        result["ingest"] = ingest_result
    return result


def build_graph_context_pack(scope: Path, out_root: Path, goal: str) -> dict:
    scope = scope.expanduser().resolve()
    out_root = out_root.expanduser().resolve()
    manifests = out_root / "manifests"
    packs = ensure_dir(out_root / "packs")

    documents = read_jsonl(manifests / "documents.jsonl")
    chunks = read_jsonl(manifests / "chunks.jsonl")
    failures = read_jsonl(manifests / "failures.jsonl")
    chunks_by_doc = group_chunks_by_doc(chunks)

    graph = build_context_graph(documents, chunks_by_doc, goal, scope)
    selected = rank_graph_documents(graph, chunks_by_doc)[:MAX_SOURCES]

    created_at = datetime.now().astimezone().isoformat()
    task_id = f"{slugify(goal)}-route-b-graph-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    pack_dir = ensure_dir(packs / task_id)
    context_path = pack_dir / "context.md"
    sources_path = pack_dir / "sources.jsonl"
    manifest_path = pack_dir / "manifest.json"
    graph_path = pack_dir / "context_graph.json"

    sources = [render_graph_source(item, chunks_by_doc) for item in selected]
    manifest = {
        "context_pack_version": "0.1",
        "route": "b_graph_context_map",
        "task_id": task_id,
        "goal": goal,
        "scope": str(scope),
        "created_at": created_at,
        "documents_considered": len(documents),
        "sources_included": len(sources),
        "graph_nodes": len(graph["nodes"]),
        "graph_edges": len(graph["edges"]),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "context_graph_path": str(graph_path),
    }

    write_jsonl(sources_path, sources)
    write_text(graph_path, json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(context_path, render_graph_context(goal, scope, created_at, documents, failures, graph, sources))

    return {
        "task_id": task_id,
        "route": "b_graph_context_map",
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "context_graph_path": str(graph_path),
        "sources_included": len(sources),
        "graph_nodes": len(graph["nodes"]),
        "graph_edges": len(graph["edges"]),
    }


def group_chunks_by_doc(chunks: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        grouped[chunk["doc_id"]].append(chunk)
    for record_chunks in grouped.values():
        record_chunks.sort(key=lambda item: item["chunk_index"])
    return dict(grouped)


def build_context_graph(documents: list[dict], chunks_by_doc: dict[str, list[dict]], goal: str, scope: Path) -> dict:
    terms = graph_terms_for(goal, scope)
    nodes = []
    edges = []
    folder_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()

    for doc in documents:
        folder = folder_for(doc)
        extension = doc.get("extension") or "[none]"
        folder_counts[folder] += 1
        extension_counts[extension] += 1

    for folder, count in sorted(folder_counts.items()):
        nodes.append({"id": f"folder:{folder}", "type": "folder", "name": folder, "doc_count": count})
    for extension, count in sorted(extension_counts.items()):
        nodes.append({"id": f"extension:{extension}", "type": "extension", "name": extension, "doc_count": count})
    for term in terms:
        nodes.append({"id": f"term:{term}", "type": "goal_term", "name": term})

    for doc in documents:
        doc_chunks = chunks_by_doc.get(doc["doc_id"], [])
        matched_terms = matched_goal_terms(doc, doc_chunks, terms)
        node = {
            "id": doc["doc_id"],
            "type": "document",
            "name": doc.get("relative_path") or doc.get("path"),
            "path": doc.get("path"),
            "relative_path": doc.get("relative_path"),
            "policy": doc.get("policy"),
            "parser": doc.get("parser"),
            "status": doc.get("status"),
            "extension": doc.get("extension") or "[none]",
            "size_bytes": doc.get("size_bytes", 0),
            "mtime": doc.get("mtime"),
            "chunk_count": len(doc_chunks),
            "text_chars": doc.get("text_chars", 0),
            "matched_terms": matched_terms,
        }
        nodes.append(node)
        edges.append({"source": f"folder:{folder_for(doc)}", "target": doc["doc_id"], "type": "contains", "weight": 1.0})
        edges.append(
            {
                "source": f"extension:{doc.get('extension') or '[none]'}",
                "target": doc["doc_id"],
                "type": "classifies",
                "weight": 0.7,
            }
        )
        for term in matched_terms:
            edges.append({"source": f"term:{term}", "target": doc["doc_id"], "type": "matches_goal", "weight": 0.9})
        if doc_chunks:
            nodes.append(
                {
                    "id": f"chunks:{doc['doc_id']}",
                    "type": "chunk_group",
                    "name": f"chunks:{doc.get('relative_path') or doc.get('path')}",
                    "doc_id": doc["doc_id"],
                    "chunk_count": len(doc_chunks),
                }
            )
            edges.append({"source": doc["doc_id"], "target": f"chunks:{doc['doc_id']}", "type": "has_chunks", "weight": 0.8})

    return {
        "version": "0.1",
        "kind": "agent-context-graph-lite",
        "goal": goal,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "documents": len(documents),
            "folders": len(folder_counts),
            "extensions": len(extension_counts),
            "goal_terms": len(terms),
        },
    }


def rank_graph_documents(graph: dict, chunks_by_doc: dict[str, list[dict]]) -> list[dict]:
    ranked = []
    has_goal_terms = graph.get("stats", {}).get("goal_terms", 0) > 0
    for node in graph["nodes"]:
        if node.get("type") != "document":
            continue
        matched_terms = node.get("matched_terms", [])
        chunk_count = node.get("chunk_count", 0)
        extracted_bonus = 1.0 if node.get("policy") != "metadata_only" else 0.2
        status_penalty = -1.0 if node.get("status") == "failed" else 0.0
        text_bonus = min(node.get("text_chars", 0), 4000) / 4000
        score = len(matched_terms) * 2.0 + min(chunk_count, 4) * 0.35 + extracted_bonus + text_bonus + status_penalty
        if has_goal_terms and not matched_terms:
            score *= 0.25
        if score <= 0:
            score = 0.05
        ranked.append({"score": score, "node": node, "chunks": chunks_by_doc.get(node["id"], [])})
    ranked.sort(key=lambda item: (-item["score"], item["node"].get("relative_path") or ""))
    return ranked


def render_graph_source(item: dict, chunks_by_doc: dict[str, list[dict]]) -> dict:
    node = item["node"]
    chunks = chunks_by_doc.get(node["id"], [])
    best_chunk = chunks[0] if chunks else None
    return {
        "type": "graph_document",
        "score": round(item["score"], 4),
        "doc_id": node["id"],
        "path": node.get("path"),
        "relative_path": node.get("relative_path"),
        "parser": node.get("parser"),
        "status": node.get("status"),
        "policy": node.get("policy"),
        "matched_terms": node.get("matched_terms", []),
        "chunk_count": node.get("chunk_count", 0),
        "snippet": snippet(best_chunk["text"]) if best_chunk else metadata_snippet(node),
    }


def render_graph_context(
    goal: str,
    scope: Path,
    created_at: str,
    documents: list[dict],
    failures: list[dict],
    graph: dict,
    sources: list[dict],
) -> str:
    metadata_count = sum(1 for doc in documents if doc.get("policy") == "metadata_only")
    extracted_count = sum(1 for doc in documents if doc.get("extracted_md_path"))
    top_edges = Counter(edge["type"] for edge in graph["edges"]).most_common()

    lines = [
        "---",
        "context_pack_version: 0.1",
        "route: b_graph_context_map",
        f"goal: {goal}",
        f"scope: {scope}",
        f"created_at: {created_at}",
        "---",
        "",
        "# Task",
        "",
        goal,
        "",
        "# Route B Hypothesis",
        "",
        "Use a graph-lite context map before reading snippets: folders, file types, goal terms, documents, and chunks are explicit nodes or edges.",
        "",
        "# Graph Summary",
        "",
        f"- Scope scanned: `{scope}`",
        f"- Documents considered: {len(documents)}",
        f"- Extracted documents: {extracted_count}",
        f"- Metadata-only documents: {metadata_count}",
        f"- Failure records: {len(failures)}",
        f"- Graph nodes: {len(graph['nodes'])}",
        f"- Graph edges: {len(graph['edges'])}",
        f"- Edge types: {', '.join(f'{name}={count}' for name, count in top_edges) or 'none'}",
        "",
        "# Selected Context Map",
        "",
    ]

    if sources:
        for source in sources:
            terms = ", ".join(source.get("matched_terms", [])) or "no direct goal-term match"
            lines.append(
                f"- `{source['path']}` score={source['score']} parser={source.get('parser')} chunks={source.get('chunk_count')} terms={terms}"
            )
    else:
        lines.append("- No graph sources were selected.")

    lines.extend(["", "# Evidence Snippets", ""])
    for source in sources[:10]:
        lines.append(f"- `{source.get('relative_path') or source.get('path')}`: {source['snippet']}")

    lines.extend(["", "# Limitations", ""])
    lines.append("- Route B is graph-lite: it models file/folder/type/term relations, not a full code AST or ontology.")
    lines.append("- If a scanned scope contains a code repository with `.understand-anything/knowledge-graph.json`, a future adapter should import that graph instead of rebuilding a shallow map.")
    lines.append("- Scores are deterministic and local; no embeddings or LLM summaries are used.")
    if failures:
        lines.append(f"- `{len(failures)}` extraction failures were recorded in `manifests/failures.jsonl`.")

    lines.extend(["", "# Recommended Next Actions", ""])
    lines.append("- Compare this route with Route A on whether it surfaces better folders/file types before snippets.")
    lines.append("- Use Route A when direct quotes matter; use Route B when orientation and source-map coverage matter.")
    lines.append("- Add recall events after human review so selected graph edges can gain or lose weight over time.")
    lines.append("")
    return "\n".join(lines)


def write_comparison_report(out_root: Path, goal: str, scope: Path, route_a: dict, route_b: dict) -> Path:
    report_path = out_root / "reports" / "ab_comparison_report.md"
    a_sources = read_jsonl(Path(route_a["sources_jsonl_path"]))
    b_sources = read_jsonl(Path(route_b["sources_jsonl_path"]))
    a_paths = {source.get("path") for source in a_sources}
    b_paths = {source.get("path") for source in b_sources}
    overlap = sorted(path for path in (a_paths & b_paths) if path)

    lines = [
        "# A/B Route Comparison",
        "",
        f"- Goal: {goal}",
        f"- Scope: `{scope}`",
        "",
        "## Route A: Chunk Pack",
        "",
        "- Strategy: rank extracted chunks and metadata-only records directly against the task goal.",
        f"- Context: `{route_a['context_md_path']}`",
        f"- Sources: {len(a_sources)}",
        f"- Chunk sources: {sum(1 for source in a_sources if source.get('type') == 'chunk')}",
        f"- Metadata-only sources: {sum(1 for source in a_sources if source.get('type') == 'metadata_only')}",
        "",
        "## Route B: Graph Context Map",
        "",
        "- Strategy: build a graph-lite map of folders, extensions, goal terms, documents, and chunk availability before ranking documents.",
        f"- Context: `{route_b['context_md_path']}`",
        f"- Sources: {len(b_sources)}",
        f"- Graph nodes: {route_b['graph_nodes']}",
        f"- Graph edges: {route_b['graph_edges']}",
        "",
        "## Overlap",
        "",
        f"- Shared source paths: {len(overlap)}",
    ]
    for path in overlap[:20]:
        lines.append(f"- `{path}`")

    lines.extend(
        [
            "",
            "## Reading Guide",
            "",
            "- Prefer Route A if the task needs direct excerpts and immediate source quotes.",
            "- Prefer Route B if the task needs orientation, source coverage, and relationship-aware triage.",
            "- If both routes agree on a path, inspect it first; agreement is the strongest local signal in this experiment.",
            "",
        ]
    )
    write_text(report_path, "\n".join(lines))
    return report_path


def matched_goal_terms(doc: dict, chunks: list[dict], terms: list[str]) -> list[str]:
    haystack = f"{doc.get('relative_path', '')}\n{doc.get('extension', '')}".lower()
    for chunk in chunks[:4]:
        haystack += "\n" + chunk.get("text", "").lower()
    return [term for term in terms if term in haystack]


def graph_terms_for(goal: str, scope: Path) -> list[str]:
    scope_words = {scope.name.lower(), scope.name.lower().rstrip("s")}
    stop_terms = {
        "download",
        "downloads",
        "分析",
        "文件",
        "哪些",
        "适合",
        "进入",
        "里面",
    }
    terms = []
    for term in terms_for(goal):
        if term in stop_terms or term in scope_words or len(term) > 16:
            continue
        terms.append(term)

    goal_lower = goal.lower()
    expansion_terms = ["个人助手", "长期记忆", "记忆", "助手"]
    if "记忆" in goal_lower or "助手" in goal_lower:
        expansion_terms.extend(["memory", "context", "skill", "workflow", "decision", "工作流", "决策", "上下文", "permalink"])
    for term in expansion_terms:
        if term in goal_lower:
            terms.append(term)
        elif term.isascii() or term in {"工作流", "决策", "上下文", "permalink"}:
            terms.append(term)

    deduped = []
    for term in terms:
        if term not in deduped:
            deduped.append(term)
    return deduped or [term for term in terms_for(goal) if term not in scope_words] or terms_for(goal)


def folder_for(doc: dict) -> str:
    relative = Path(doc.get("relative_path") or doc.get("path") or "")
    parent = str(relative.parent)
    return "." if parent in {"", "."} else parent
