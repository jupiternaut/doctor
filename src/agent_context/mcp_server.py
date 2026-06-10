from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .cold_index import build_cold_index, index_path_for, query_cold_index
from .ingest import ingest_scope
from .io import ensure_dir, read_jsonl
from .pack import build_context_pack


MCP_VERSION = "0.1"
DEFAULT_ROOT_ENV = "AGENT_CONTEXT_ROOT"


def default_out_root() -> Path:
    return Path(os.environ.get(DEFAULT_ROOT_ENV, ".")).expanduser().resolve()


def resolve_out_root(out_root: str | None = None) -> Path:
    if out_root:
        return Path(out_root).expanduser().resolve()
    return default_out_root()


def mcp_index_context(out_root: str | None = None) -> dict[str, Any]:
    return build_cold_index(resolve_out_root(out_root))


def mcp_search_context(query: str, limit: int = 12, out_root: str | None = None) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    result = query_cold_index(root, query, limit=max(1, limit))
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    return {
        "mcp_version": MCP_VERSION,
        **result,
        "top_sources": [
            {
                "score": source.get("score"),
                "path": source.get("path"),
                "relative_path": source.get("relative_path"),
                "type": source.get("type"),
                "source_id": source.get("source_id"),
                "source_chunk_id": source.get("source_chunk_id"),
                "snippet": source.get("snippet"),
            }
            for source in sources[:limit]
        ],
    }


def mcp_build_hot_pack(
    scope: str,
    goal: str,
    out_root: str | None = None,
    with_index: bool = False,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    ingest_result = ingest_scope(Path(scope), root)
    pack_result = build_context_pack(Path(scope), root, goal)
    result: dict[str, Any] = {
        "mcp_version": MCP_VERSION,
        "ingest": ingest_result,
        "pack": pack_result,
    }
    if with_index:
        result["index"] = build_cold_index(root)
    return result


def mcp_read_source(identifier: str, out_root: str | None = None, max_chars: int = 4000) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    max_chars = max(200, min(max_chars, 20_000))
    db_path = index_path_for(root)
    if db_path.exists():
        record = lookup_index_record(db_path, identifier)
        if record:
            return {
                "mcp_version": MCP_VERSION,
                "identifier": identifier,
                **record,
                "warnings": text_warnings(record.get("text") or ""),
                "text": trim_text(record.get("text") or "", max_chars),
            }

    path = Path(identifier).expanduser()
    if not path.is_absolute():
        path = (root / identifier).resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"source not found in index or filesystem: {identifier}")

    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "mcp_version": MCP_VERSION,
        "identifier": identifier,
        "type": "file",
        "path": str(path),
        "warnings": text_warnings(text),
        "text": trim_text(text, max_chars),
    }


def lookup_index_record(db_path: Path, identifier: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        chunk = conn.execute(
            """
            SELECT c.*, d.parser, d.policy, d.status, d.extension
            FROM chunks c
            LEFT JOIN documents d ON d.source_id = c.source_id
            WHERE c.source_chunk_id = ?
               OR c.chunk_id = ?
               OR c.path = ?
               OR c.relative_path = ?
            ORDER BY c.chunk_index
            LIMIT 1
            """,
            (identifier, identifier, identifier, identifier),
        ).fetchone()
        if chunk:
            return {
                "type": "chunk",
                "source_chunk_id": chunk["source_chunk_id"],
                "source_id": chunk["source_id"],
                "chunk_id": chunk["chunk_id"],
                "doc_id": chunk["doc_id"],
                "path": chunk["path"],
                "relative_path": chunk["relative_path"],
                "chunk_index": chunk["chunk_index"],
                "parser": chunk["parser"],
                "policy": chunk["policy"],
                "status": chunk["status"],
                "extension": chunk["extension"],
                "text": chunk["text"],
            }

        document = conn.execute(
            """
            SELECT *
            FROM documents
            WHERE source_id = ?
               OR doc_id = ?
               OR path = ?
               OR relative_path = ?
            LIMIT 1
            """,
            (identifier, identifier, identifier, identifier),
        ).fetchone()
        if document:
            return {
                "type": "document",
                "source_id": document["source_id"],
                "doc_id": document["doc_id"],
                "path": document["path"],
                "relative_path": document["relative_path"],
                "parser": document["parser"],
                "policy": document["policy"],
                "status": document["status"],
                "extension": document["extension"],
                "text": document_text(document),
            }
    finally:
        conn.close()
    return None


def document_text(document: sqlite3.Row) -> str:
    extracted = document["extracted_md_path"]
    if extracted:
        path = Path(extracted)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return (
        f"metadata-only source; path={document['path']}; "
        f"relative_path={document['relative_path']}; extension={document['extension']}; "
        f"parser={document['parser']}; policy={document['policy']}; status={document['status']}"
    )


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 80].rstrip() + "\n\n[agent-context: truncated]"


def text_warnings(text: str) -> list[str]:
    if not text:
        return ["empty text"]
    sample = text[:2000]
    replacement_count = sample.count("\ufffd")
    control_count = sum(1 for char in sample if ord(char) < 32 and char not in "\n\r\t")
    warnings = []
    if replacement_count / max(len(sample), 1) > 0.02:
        warnings.append("text contains many replacement characters; source may be binary or incorrectly decoded")
    if control_count / max(len(sample), 1) > 0.02:
        warnings.append("text contains many control characters; source may be binary")
    return warnings


def mcp_record_feedback(
    query_id: str,
    selected_source: str,
    reason: str = "",
    rating: int | None = None,
    out_root: str | None = None,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    feedback_dir = ensure_dir(root / "feedback")
    feedback_path = feedback_dir / "mcp_feedback.jsonl"
    record = {
        "mcp_version": MCP_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "query_id": query_id,
        "selected_source": selected_source,
        "reason": reason,
        "rating": rating,
    }
    with feedback_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return {"feedback_path": str(feedback_path), "record": record}


def create_mcp_server(out_root: str | None = None) -> FastMCP:
    root = resolve_out_root(out_root)
    server = FastMCP(
        "agent-context",
        instructions=(
            "Search and build local agent context packs from an agent-context-system "
            "workspace. Use search_context first, then read_source for specific evidence."
        ),
    )

    @server.tool()
    def search_context(query: str, limit: int = 12) -> dict[str, Any]:
        """Search the local cold index and write a RAG context pack."""
        return mcp_search_context(query=query, limit=limit, out_root=str(root))

    @server.tool()
    def index_context() -> dict[str, Any]:
        """Rebuild the SQLite cold index from existing manifests."""
        return mcp_index_context(out_root=str(root))

    @server.tool()
    def build_hot_pack(scope: str, goal: str, with_index: bool = False) -> dict[str, Any]:
        """Scan a scope and build a Codex-readable hot context pack."""
        return mcp_build_hot_pack(scope=scope, goal=goal, out_root=str(root), with_index=with_index)

    @server.tool()
    def read_source(identifier: str, max_chars: int = 4000) -> dict[str, Any]:
        """Read a source by path, source_id, source_chunk_id, chunk_id, doc_id, or relative path."""
        return mcp_read_source(identifier=identifier, out_root=str(root), max_chars=max_chars)

    @server.tool()
    def record_feedback(query_id: str, selected_source: str, reason: str = "", rating: int | None = None) -> dict[str, Any]:
        """Record user feedback for a returned context source."""
        return mcp_record_feedback(
            query_id=query_id,
            selected_source=selected_source,
            reason=reason,
            rating=rating,
            out_root=str(root),
        )

    return server


def run_mcp_server(out_root: str | None = None) -> None:
    create_mcp_server(out_root).run("stdio")
