from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .cold_index import (
    create_schema,
    insert_chunks,
    insert_documents,
    insert_failures,
    read_meta,
    render_sources,
    reset_sqlite_database,
    retrieve_candidates,
    write_meta,
)
from .ingest import chunk_text
from .io import ensure_dir, read_jsonl, write_jsonl
from .providers import (
    claude_session_messages,
    codex_session_messages,
    ensure_provider_manifests,
    load_session_records,
    session_transcript_text,
)
from .retrieval_backends import RetrievalConfig, backend_meta, default_retrieval_config

SESSION_INDEX_VERSION = "0.1"
SESSION_PARSER_VERSION = f"agent-context-session-index-v{SESSION_INDEX_VERSION}"
DEFAULT_MAX_INDEX_SESSIONS = 300
DEFAULT_MAX_MESSAGES_PER_SESSION = 1000


@dataclass(frozen=True)
class SessionIndexPaths:
    root: Path
    manifests: Path
    indexes: Path

    @classmethod
    def from_root(cls, root: Path) -> "SessionIndexPaths":
        return cls(root=root, manifests=root / "manifests", indexes=root / "indexes")

    @property
    def documents_jsonl(self) -> Path:
        return self.manifests / "session_documents.jsonl"

    @property
    def chunks_jsonl(self) -> Path:
        return self.manifests / "session_chunks.jsonl"

    @property
    def failures_jsonl(self) -> Path:
        return self.manifests / "session_failures.jsonl"

    @property
    def sqlite(self) -> Path:
        return self.indexes / "sessions.sqlite"


def session_index_path_for(out_root: Path) -> Path:
    return SessionIndexPaths.from_root(out_root.expanduser().resolve()).sqlite


def build_session_index(
    out_root: Path,
    *,
    max_sessions: int = DEFAULT_MAX_INDEX_SESSIONS,
    max_messages_per_session: int = DEFAULT_MAX_MESSAGES_PER_SESSION,
    retrieval_config: RetrievalConfig | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    retrieval_config = retrieval_config or default_retrieval_config()
    paths = SessionIndexPaths.from_root(out_root)
    ensure_dir(paths.manifests)
    ensure_dir(paths.indexes)
    ensure_provider_manifests(out_root)

    sessions = load_session_records(out_root)[: max(1, max_sessions)]
    documents: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for session in sessions:
        try:
            document, document_chunks = index_session_record(session, max_messages_per_session=max_messages_per_session)
        except Exception as exc:
            failures.append(session_failure_record(session, exc))
            continue
        documents.append(document)
        chunks.extend(document_chunks)

    documents.sort(key=lambda item: (item.get("updated_at") or "", item.get("path") or ""), reverse=True)
    chunks.sort(key=lambda item: item["chunk_id"])
    failures.sort(key=lambda item: item.get("path") or "")

    write_jsonl(paths.documents_jsonl, documents)
    write_jsonl(paths.chunks_jsonl, chunks)
    write_jsonl(paths.failures_jsonl, failures)
    build_session_sqlite(paths, documents, chunks, failures, retrieval_config=retrieval_config)

    return {
        "session_index_version": SESSION_INDEX_VERSION,
        "sessions": len(sessions),
        "documents": len(documents),
        "chunks": len(chunks),
        "failures": len(failures),
        "documents_jsonl": str(paths.documents_jsonl),
        "chunks_jsonl": str(paths.chunks_jsonl),
        "failures_jsonl": str(paths.failures_jsonl),
        "index_path": str(paths.sqlite),
        **backend_meta(retrieval_config),
    }


def build_session_sqlite(
    paths: SessionIndexPaths,
    documents: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    *,
    retrieval_config: RetrievalConfig | None = None,
) -> None:
    retrieval_config = retrieval_config or default_retrieval_config()
    reset_sqlite_database(paths.sqlite)
    conn = sqlite3.connect(paths.sqlite)
    conn.row_factory = sqlite3.Row
    try:
        fts_enabled = create_schema(conn)
        insert_documents(conn, documents)
        insert_chunks(conn, chunks, documents, fts_enabled, retrieval_config=retrieval_config)
        insert_failures(conn, failures)
        write_meta(
            conn,
            {
                "index_version": SESSION_INDEX_VERSION,
                "index_kind": "session_transcripts",
                "built_at": datetime.now().astimezone().isoformat(),
                "documents": str(conn.execute("SELECT count(*) FROM documents").fetchone()[0]),
                "chunks": str(conn.execute("SELECT count(*) FROM chunks").fetchone()[0]),
                "failures": str(conn.execute("SELECT count(*) FROM failures").fetchone()[0]),
                "fts_enabled": "true" if fts_enabled else "false",
                **backend_meta(retrieval_config),
            },
        )
        conn.commit()
    finally:
        conn.close()


def search_session_index(
    out_root: Path,
    query: str,
    limit: int = 12,
    *,
    retrieval_config: RetrievalConfig | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    paths = SessionIndexPaths.from_root(out_root)
    if not paths.sqlite.exists():
        raise FileNotFoundError(f"session index not found: {paths.sqlite}")

    conn = sqlite3.connect(paths.sqlite)
    conn.row_factory = sqlite3.Row
    try:
        meta = read_meta(conn)
        candidates = retrieve_candidates(conn, query, max(1, limit), meta, retrieval_config=retrieval_config)
        sources = annotate_session_sources(out_root, render_sources(candidates[:limit]))
    finally:
        conn.close()

    return {
        "query": query,
        "index_path": str(paths.sqlite),
        "retrieval_mode": "session_hybrid_fts_vector_lite_path",
        "limit": limit,
        "sources_included": len(sources),
        "sources": sources,
        "index_meta": meta,
    }


def annotate_session_sources(out_root: Path, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents_by_path = {record["path"]: record for record in read_jsonl(SessionIndexPaths.from_root(out_root).documents_jsonl)}
    for source in sources:
        document = documents_by_path.get(source["path"], {})
        source["type"] = "session_chunk" if source.get("source_chunk_id") else "session_document"
        source["source_group"] = "codex_sessions"
        source["provider"] = "session_index"
        source["session_provider"] = document.get("session_provider")
        source["provider_source_id"] = document.get("provider_source_id")
        source["session_id"] = document.get("session_id")
        source["thread_name"] = document.get("thread_name")
        source["cwd"] = document.get("cwd")
    return sources


def index_session_record(session: dict[str, Any], *, max_messages_per_session: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(str(session.get("path") or ""))
    text = session_index_text(session, max_messages=max(1, max_messages_per_session))
    encoded = text.encode("utf-8", errors="replace")
    doc_id = f"session-transcript:{session.get('provider')}:{session.get('session_id') or stable_id(str(path))}"
    stat = path.stat() if path.exists() else None
    document = {
        "doc_id": doc_id,
        "path": str(path),
        "relative_path": session.get("relative_path") or path.name,
        "scope": session.get("cwd") or str(path.parent),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": stat.st_size if stat else len(encoded),
        "mtime": session.get("updated_at") or datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat() if stat else "",
        "extension": ".jsonl",
        "mime": "application/jsonl",
        "policy": "session_transcript_index",
        "parser": str(session.get("provider") or "agent_session_jsonl"),
        "parser_version": SESSION_PARSER_VERSION,
        "status": "ok",
        "extracted_md_path": None,
        "text_chars": len(text),
        "chunk_count": 0,
        "provider": "session_index",
        "session_provider": session.get("provider"),
        "provider_source_id": session.get("source_id"),
        "session_id": session.get("session_id"),
        "thread_name": session.get("thread_name"),
        "cwd": session.get("cwd"),
        "updated_at": session.get("updated_at"),
    }
    chunks = chunk_text(doc_id, path, text)
    for chunk in chunks:
        chunk["relative_path"] = document["relative_path"]
        chunk["provider"] = "session_index"
        chunk["session_provider"] = session.get("provider")
        chunk["provider_source_id"] = session.get("source_id")
        chunk["session_id"] = session.get("session_id")
        chunk["thread_name"] = session.get("thread_name")
        chunk["cwd"] = session.get("cwd")
    document["chunk_count"] = len(chunks)
    return document, chunks


def session_index_text(session: dict[str, Any], *, max_messages: int) -> str:
    provider = str(session.get("provider") or "")
    path = Path(str(session.get("path") or ""))
    if provider == "codex_session" and path.exists():
        messages = codex_session_messages(path)
    elif provider == "claude_session" and path.exists():
        messages = claude_session_messages(path)
    else:
        messages = []
    if not messages:
        return session_transcript_text(session, max_messages=max_messages)

    lines = [
        f"thread_name: {session.get('thread_name') or ''}",
        f"provider: {provider}",
        f"session_id: {session.get('session_id') or ''}",
        f"cwd: {session.get('cwd') or ''}",
        "",
        "# Messages",
    ]
    for index, message in enumerate(messages[:max_messages], start=1):
        role = message.get("role") or "message"
        timestamp = message.get("timestamp") or ""
        lines.extend(["", f"## {index}. {role} {timestamp}".rstrip(), str(message.get("text") or "")])
    omitted = len(messages) - max_messages
    if omitted > 0:
        lines.extend(["", f"omitted_messages: {omitted}"])
    lines.extend(["", "# Source", f"path: {session.get('path') or ''}", f"relative_path: {session.get('relative_path') or ''}"])
    return "\n".join(lines).strip()


def session_failure_record(session: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "path": session.get("path") or "",
        "sha256": "",
        "stage": "session_index",
        "parser": str(session.get("provider") or "agent_session_jsonl"),
        "error_type": type(exc).__name__,
        "error": str(exc),
        "recoverable": True,
        "session_id": session.get("session_id"),
        "thread_name": session.get("thread_name"),
    }


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
