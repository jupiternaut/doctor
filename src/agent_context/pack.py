from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .io import ensure_dir, read_jsonl, write_jsonl, write_text

MAX_SOURCES = 20


def build_context_pack(scope: Path, out_root: Path, goal: str) -> dict:
    scope = scope.expanduser().resolve()
    out_root = out_root.expanduser().resolve()
    manifests = out_root / "manifests"
    packs = ensure_dir(out_root / "packs")

    documents = read_jsonl(manifests / "documents.jsonl")
    chunks = read_jsonl(manifests / "chunks.jsonl")
    failures = read_jsonl(manifests / "failures.jsonl")

    documents_by_id = {record["doc_id"]: record for record in documents}
    selected_chunks = rank_chunks(chunks, documents_by_id, goal)[:MAX_SOURCES]
    metadata_sources = rank_metadata_sources(documents, goal)[:MAX_SOURCES]

    created_at = datetime.now().astimezone().isoformat()
    task_id = f"{slugify(goal)}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    pack_dir = ensure_dir(packs / task_id)
    sources_path = pack_dir / "sources.jsonl"
    manifest_path = pack_dir / "manifest.json"
    context_path = pack_dir / "context.md"

    sources = []
    for score, chunk in selected_chunks:
        doc = documents_by_id.get(chunk["doc_id"], {})
        sources.append(
            {
                "type": "chunk",
                "score": round(score, 4),
                "chunk_id": chunk["chunk_id"],
                "doc_id": chunk["doc_id"],
                "path": chunk["path"],
                "relative_path": doc.get("relative_path"),
                "parser": doc.get("parser"),
                "status": doc.get("status"),
                "snippet": snippet(chunk["text"]),
            }
        )
    for score, doc in metadata_sources:
        sources.append(
            {
                "type": "metadata_only",
                "score": round(score, 4),
                "doc_id": doc["doc_id"],
                "path": doc["path"],
                "relative_path": doc.get("relative_path"),
                "parser": doc.get("parser"),
                "status": doc.get("status"),
                "snippet": metadata_snippet(doc),
            }
        )

    manifest = {
        "context_pack_version": "0.1",
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
    write_text(context_path, render_context(goal, scope, created_at, documents, failures, sources, selected_chunks, documents_by_id))

    return {
        "task_id": task_id,
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "sources_included": len(sources),
    }


def rank_chunks(chunks: list[dict], documents_by_id: dict[str, dict], goal: str) -> list[tuple[float, dict]]:
    terms = terms_for(goal)
    ranked: list[tuple[float, dict]] = []
    for chunk in chunks:
        doc = documents_by_id.get(chunk["doc_id"], {})
        haystack = f"{chunk.get('text', '')}\n{chunk.get('path', '')}\n{doc.get('relative_path', '')}".lower()
        overlap = sum(1 for term in terms if term in haystack)
        path_bonus = sum(1 for term in terms if term in str(doc.get("relative_path", "")).lower()) * 0.5
        parser_bonus = 1.0 if doc.get("parser") != "metadata_only" else 0.0
        score = overlap + path_bonus + parser_bonus
        if score <= 0:
            score = 0.1
        ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["chunk_index"]))
    return ranked


def rank_metadata_sources(documents: list[dict], goal: str) -> list[tuple[float, dict]]:
    terms = terms_for(goal)
    ranked: list[tuple[float, dict]] = []
    for doc in documents:
        if doc.get("policy") != "metadata_only":
            continue
        haystack = f"{doc.get('relative_path', '')} {doc.get('path', '')} {doc.get('extension', '')}".lower()
        overlap = sum(1 for term in terms if term in haystack)
        score = overlap + 0.2
        ranked.append((score, doc))
    ranked.sort(key=lambda item: (-item[0], item[1]["relative_path"]))
    return ranked


def terms_for(goal: str) -> list[str]:
    raw_terms = re.findall(r"[\w\u4e00-\u9fff]+", goal.lower())
    terms = []
    for term in raw_terms:
        if len(term) >= 2 and term not in {"the", "and", "for", "with"}:
            terms.append(term)
    return terms or [goal.lower()]


def slugify(value: str) -> str:
    ascii_words = re.findall(r"[a-zA-Z0-9]+", value.lower())
    if ascii_words:
        slug = "-".join(ascii_words[:8])
    else:
        slug = "context-pack"
    return slug[:60].strip("-") or "context-pack"


def snippet(text: str, limit: int = 420) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def metadata_snippet(doc: dict) -> str:
    return (
        f"metadata-only source; extension={doc.get('extension')}; "
        f"size_bytes={doc.get('size_bytes')}; parser={doc.get('parser')}"
    )


def render_context(
    goal: str,
    scope: Path,
    created_at: str,
    documents: list[dict],
    failures: list[dict],
    sources: list[dict],
    selected_chunks: list[tuple[float, dict]],
    documents_by_id: dict[str, dict],
) -> str:
    metadata_count = sum(1 for doc in documents if doc.get("policy") == "metadata_only")
    failed_doc_count = sum(1 for doc in documents if doc.get("status") == "failed")
    failure_count = len(failures)
    extracted_count = sum(1 for doc in documents if doc.get("extracted_md_path"))

    lines = [
        "---",
        "context_pack_version: 0.1",
        f"goal: {goal}",
        f"scope: {scope}",
        f"created_at: {created_at}",
        "---",
        "",
        "# Task",
        "",
        goal,
        "",
        "# Must Know",
        "",
        f"- Scope scanned: `{scope}`",
        f"- Documents considered: {len(documents)}",
        f"- Extracted documents: {extracted_count}",
        f"- Metadata-only documents: {metadata_count}",
        f"- Failed document records: {failed_doc_count}",
        f"- Failure records: {failure_count}",
        f"- Sources included in this pack: {len(sources)}",
        "",
        "# Relevant Files",
        "",
    ]

    if sources:
        for source in sources[:MAX_SOURCES]:
            lines.append(
                f"- `{source['path']}` ({source['type']}, score={source['score']}, parser={source.get('parser')})"
            )
    else:
        lines.append("- No relevant sources were selected.")

    lines.extend(["", "# Extracted Facts", ""])
    if selected_chunks:
        for score, chunk in selected_chunks[:10]:
            doc = documents_by_id.get(chunk["doc_id"], {})
            lines.append(f"- `{doc.get('relative_path', chunk['path'])}`: {snippet(chunk['text'], 260)}")
    else:
        lines.append("- No extracted text chunks are available yet.")

    lines.extend(["", "# Source Quotes", ""])
    chunk_sources = [source for source in sources if source["type"] == "chunk"]
    if chunk_sources:
        for source in chunk_sources[:8]:
            lines.append(f"> {source['snippet']}")
            lines.append(f">")
            lines.append(f"> Source: `{source['path']}`")
            lines.append("")
    else:
        lines.append("- No source quotes are available because no extracted chunks were selected.")

    lines.extend(["# Limitations", ""])
    lines.append("- v0.1 uses deterministic keyword/path scoring; no embeddings or LLM summarization are used.")
    lines.append("- Archives, packages, images, audio, and video are metadata-only and are not expanded or transcribed.")
    lines.append("- OCR is not implemented in v0.1.")
    if failures:
        lines.append(f"- `{len(failures)}` extraction failures were recorded in `manifests/failures.jsonl`.")
    if metadata_count:
        lines.append(f"- `{metadata_count}` metadata-only files may still contain useful information not visible in this pack.")

    lines.extend(["", "# Recommended Next Actions", ""])
    lines.append("- Inspect `reports/downloads_ingestion_report.md` for extraction coverage and failures.")
    lines.append("- Review `manifests/failures.jsonl` before deciding whether to add Docling, OCR, or audio transcription.")
    lines.append("- Promote useful files from this pack into long-term memory only after reading the cited source paths.")
    lines.append("- Re-run `agent-context build` after files change; unchanged files should be skipped incrementally.")
    lines.append("")
    return "\n".join(lines)
