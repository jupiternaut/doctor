from __future__ import annotations

import json
import os
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir
from .retrieval_backends import (
    EXACT_SCAN_BACKEND_ID,
    FASTEMBED_BACKEND_ID,
    HNSWLIB_BACKEND_ID,
    AnnSearchUnavailable,
    HnswlibCachePaths,
    RetrievalConfig,
    default_retrieval_config,
    embed_documents,
    get_embedding_backend,
    score_dense_rows_with_hnswlib_cached,
)


SEMANTIC_INDEX_VERSION = "0.1"
DEFAULT_SEMANTIC_BUDGET = 32
DEFAULT_SEMANTIC_TEXT_CHARS = 800
DEFAULT_SEMANTIC_QUERY_LIMIT = 12
DEFAULT_MIN_SEMANTIC_ROWS = 16
DEFAULT_REFRESH_BUCKET_CAP = 4
SEMANTIC_SOURCE_GROUPS = {
    "downloads": "downloads_documents",
    "projects": "git_repositories",
    "sessions": "codex_sessions",
}


def semantic_index_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "indexes" / "semantic.sqlite"


def run_semantic_refresh(
    out_root: Path,
    *,
    source: str = "all",
    budget: int = DEFAULT_SEMANTIC_BUDGET,
    backend: str = FASTEMBED_BACKEND_ID,
    text_chars: int = DEFAULT_SEMANTIC_TEXT_CHARS,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    db_path = semantic_index_path_for(out_root)
    ensure_dir(db_path.parent)
    normalized_budget = max(1, int(budget))
    normalized_text_chars = max(80, int(text_chars))
    created_at = datetime.now().astimezone().isoformat()
    job_id = f"semantic-refresh-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        create_semantic_schema(conn)
        candidates = select_semantic_candidates(conn, out_root, source, normalized_budget)
        if not candidates:
            write_job(conn, job_id, source, backend, normalized_budget, created_at, "noop", 0, 0, "")
            conn.commit()
            return semantic_refresh_result(db_path, job_id, source, backend, normalized_budget, 0, 0, "noop")

        try:
            embedding_backend = get_embedding_backend(RetrievalConfig(embedding_backend=backend))
            texts = [semantic_text(candidate, normalized_text_chars) for candidate in candidates]
            embeddings = embed_documents(embedding_backend, texts)
        except Exception as exc:  # noqa: BLE001 - persist failed background job state.
            write_job(conn, job_id, source, backend, normalized_budget, created_at, "failed", 0, 0, f"{type(exc).__name__}: {exc}")
            conn.commit()
            return semantic_refresh_result(db_path, job_id, source, backend, normalized_budget, 0, len(candidates), "failed", str(exc))

        rows = [semantic_row(candidate, embedding, backend, created_at) for candidate, embedding in zip(candidates, embeddings)]
        conn.executemany(
            """
            INSERT OR REPLACE INTO semantic_chunks (
              source_kind, source_chunk_id, path, relative_path, text, embedding_json,
              embedding_backend, embedding_model, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        write_meta(conn, backend)
        write_job(conn, job_id, source, backend, normalized_budget, created_at, "ok", len(rows), 0, "")
        conn.commit()
        return semantic_refresh_result(db_path, job_id, source, backend, normalized_budget, len(rows), 0, "ok")
    finally:
        conn.close()


def semantic_index_status(out_root: Path) -> dict[str, Any]:
    db_path = semantic_index_path_for(out_root)
    status: dict[str, Any] = {
        "semantic_index_version": SEMANTIC_INDEX_VERSION,
        "index_path": str(db_path),
        "exists": db_path.exists(),
    }
    if not db_path.exists():
        return status
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        create_semantic_schema(conn)
        status["chunks"] = conn.execute("SELECT count(*) FROM semantic_chunks").fetchone()[0]
        status["jobs"] = conn.execute("SELECT count(*) FROM semantic_jobs").fetchone()[0]
        status["meta"] = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
        latest = conn.execute("SELECT * FROM semantic_jobs ORDER BY created_at DESC LIMIT 1").fetchone()
        status["latest_job"] = dict(latest) if latest else None
    finally:
        conn.close()
    return status


def search_semantic_index(
    out_root: Path,
    query: str,
    *,
    limit: int = DEFAULT_SEMANTIC_QUERY_LIMIT,
    source_kinds: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    db_path = semantic_index_path_for(out_root)
    if not db_path.exists():
        raise FileNotFoundError(f"semantic index not found: {db_path}")

    normalized_limit = max(1, int(limit))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        create_semantic_schema(conn)
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
        rows = semantic_query_rows(conn, source_kinds)
        min_rows = min_semantic_rows()
        skipped_reason = ""
        if rows and len(rows) < min_rows:
            skipped_reason = f"semantic index has {len(rows)} row(s), below minimum {min_rows}"
            sources = []
        elif not rows:
            sources: list[dict[str, Any]] = []
        else:
            scores, retrieval_mode, ann_backend, ann_fallback_reason, ann_cache_status = score_semantic_rows(
                rows,
                query,
                normalized_limit,
                embedding_backend_id=meta.get("embedding_backend") or FASTEMBED_BACKEND_ID,
                out_root=out_root,
                source_kinds=source_kinds,
            )
            sources = render_semantic_sources(rows, scores, normalized_limit)
    finally:
        conn.close()

    return {
        "semantic_index_version": SEMANTIC_INDEX_VERSION,
        "query": query,
        "index_path": str(db_path),
        "retrieval_mode": retrieval_mode if rows and not skipped_reason else "semantic_skipped",
        "ann_backend": ann_backend if rows and not skipped_reason else default_retrieval_config().ann_backend,
        "ann_fallback_reason": ann_fallback_reason if rows and not skipped_reason else "",
        "ann_cache_status": ann_cache_status if rows and not skipped_reason else "",
        "limit": normalized_limit,
        "sources_included": len(sources),
        "semantic_rows_available": len(rows),
        "min_semantic_rows": min_rows,
        "skipped_reason": skipped_reason,
        "sources": sources,
        "index_meta": meta,
    }


def score_semantic_rows(
    rows: dict[str, dict],
    query: str,
    limit: int,
    *,
    embedding_backend_id: str,
    out_root: Path | None = None,
    source_kinds: list[str] | tuple[str, ...] | None = None,
) -> tuple[dict[str, float], str, str, str, str]:
    config = default_retrieval_config()
    backend = get_embedding_backend(RetrievalConfig(embedding_backend=embedding_backend_id, ann_backend=config.ann_backend))
    if config.ann_backend == HNSWLIB_BACKEND_ID:
        try:
            if getattr(backend, "storage_format", "") != "json_dense_float32":
                raise AnnSearchUnavailable(f"{backend.backend_id} embeddings are not dense float vectors")
            embed_query = getattr(backend, "embed_query", None)
            query_embedding_json = embed_query(query) if callable(embed_query) else backend.embed_document(query)
            scores, cache_status = score_dense_rows_with_hnswlib_cached(
                rows,
                query_embedding_json,
                limit,
                cache_paths=semantic_ann_cache_paths(out_root, rows, embedding_backend_id, source_kinds),
            )
            return scores, "semantic_hnswlib_ann", HNSWLIB_BACKEND_ID, "", cache_status
        except Exception as exc:  # noqa: BLE001 - ANN must never break resolver fallback.
            scores = backend.score_rows(rows, query, limit)
            return scores, "semantic_exact_vector_scan", EXACT_SCAN_BACKEND_ID, f"{type(exc).__name__}: {exc}", "fallback"

    scores = backend.score_rows(rows, query, limit)
    return scores, "semantic_exact_vector_scan", config.ann_backend or EXACT_SCAN_BACKEND_ID, "", ""


def semantic_ann_cache_paths(
    out_root: Path | None,
    rows: dict[str, dict],
    embedding_backend_id: str,
    source_kinds: list[str] | tuple[str, ...] | None,
) -> HnswlibCachePaths | None:
    if out_root is None:
        return None
    fingerprint = semantic_rows_fingerprint(rows, embedding_backend_id, source_kinds)
    cache_dir = ensure_dir(out_root.expanduser().resolve() / "indexes" / "semantic_ann")
    base = cache_dir / f"hnswlib_{fingerprint[:20]}"
    return HnswlibCachePaths(
        index_path=base.with_suffix(".bin"),
        metadata_path=base.with_suffix(".json"),
        fingerprint=fingerprint,
    )


def semantic_rows_fingerprint(
    rows: dict[str, dict],
    embedding_backend_id: str,
    source_kinds: list[str] | tuple[str, ...] | None,
) -> str:
    digest = hashlib.sha256()
    digest.update(SEMANTIC_INDEX_VERSION.encode("utf-8"))
    digest.update(str(embedding_backend_id).encode("utf-8"))
    digest.update(",".join(sorted(str(kind) for kind in source_kinds or [])).encode("utf-8"))
    for source_chunk_id, row in sorted(rows.items()):
        digest.update(str(source_chunk_id).encode("utf-8"))
        digest.update(str(row.get("source_kind") or "").encode("utf-8"))
        digest.update(str(row.get("embedding_backend") or "").encode("utf-8"))
        digest.update(str(row.get("updated_at") or "").encode("utf-8"))
        digest.update(str(row.get("embedding_json") or "").encode("utf-8"))
    return digest.hexdigest()


def create_semantic_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_chunks (
          source_kind TEXT NOT NULL,
          source_chunk_id TEXT NOT NULL,
          path TEXT,
          relative_path TEXT,
          text TEXT,
          embedding_json TEXT NOT NULL,
          embedding_backend TEXT NOT NULL,
          embedding_model TEXT,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          PRIMARY KEY (source_kind, source_chunk_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_jobs (
          job_id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          backend TEXT NOT NULL,
          budget INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          status TEXT NOT NULL,
          processed INTEGER NOT NULL,
          skipped INTEGER NOT NULL,
          error TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def semantic_query_rows(conn: sqlite3.Connection, source_kinds: list[str] | tuple[str, ...] | None = None) -> dict[str, dict]:
    params: tuple[Any, ...] = ()
    where = ""
    if source_kinds:
        placeholders = ",".join("?" for _ in source_kinds)
        where = f"WHERE source_kind IN ({placeholders})"
        params = tuple(source_kinds)
    rows = conn.execute(f"SELECT * FROM semantic_chunks {where}", params).fetchall()
    return {str(row["source_chunk_id"]): dict(row) for row in rows}


def min_semantic_rows() -> int:
    configured = os.environ.get("AGENT_CONTEXT_MIN_SEMANTIC_ROWS")
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            return DEFAULT_MIN_SEMANTIC_ROWS
    return DEFAULT_MIN_SEMANTIC_ROWS


def semantic_refresh_bucket_cap() -> int:
    configured = os.environ.get("AGENT_CONTEXT_SEMANTIC_REFRESH_BUCKET_CAP")
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            return DEFAULT_REFRESH_BUCKET_CAP
    return DEFAULT_REFRESH_BUCKET_CAP


def parse_metadata(row: dict[str, Any]) -> dict[str, Any]:
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def infer_project_path(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    current = path if path.is_dir() else path.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return str(candidate)
    return ""


def semantic_candidate_bucket(source_kind: str, row: dict[str, Any]) -> str:
    if source_kind == "projects":
        metadata = parse_metadata(row)
        project_path = metadata.get("project_path") or infer_project_path(row.get("path") or "")
        return f"project:{project_path or row.get('path') or row.get('source_id') or row.get('source_chunk_id')}"
    return f"{source_kind}:{row.get('path') or row.get('source_id') or row.get('source_chunk_id')}"


def render_semantic_sources(rows: dict[str, dict], scores: dict[str, float], limit: int) -> list[dict[str, Any]]:
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    sources: list[dict[str, Any]] = []
    for source_chunk_id, score in ranked:
        row = rows[source_chunk_id]
        metadata = parse_metadata(row)
        source_kind = row.get("source_kind") or ""
        inferred_project_path = infer_project_path(row.get("path") or "") if source_kind == "projects" else ""
        project_path = metadata.get("project_path") or inferred_project_path
        project_name = metadata.get("project_name") or (Path(project_path).name if project_path else None)
        sources.append(
            {
                "type": "semantic_chunk",
                "source_id": metadata.get("source_id") or source_chunk_id,
                "doc_id": metadata.get("doc_id") or row.get("path") or source_chunk_id,
                "source_chunk_id": source_chunk_id,
                "chunk_id": metadata.get("chunk_id"),
                "chunk_index": metadata.get("chunk_index"),
                "path": row.get("path") or "",
                "relative_path": row.get("relative_path") or row.get("path") or "",
                "score": round(float(score), 6),
                "score_parts": {"semantic": round(float(score), 6)},
                "snippet": (row.get("text") or "")[:700],
                "source_group": SEMANTIC_SOURCE_GROUPS.get(source_kind, source_kind or "semantic_index"),
                "provider": "semantic_index",
                "retrieval_channel": "semantic_index",
                "semantic_source_kind": source_kind,
                "project_id": metadata.get("project_id"),
                "project_name": project_name,
                "project_path": project_path,
                "session_id": metadata.get("session_id"),
                "thread_name": metadata.get("thread_name"),
                "session_provider": metadata.get("session_provider"),
                "provider_source_id": metadata.get("provider_source_id"),
            }
        )
    return sources


def select_semantic_candidates(conn: sqlite3.Connection, out_root: Path, source: str, budget: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    sources = ("downloads", "projects", "sessions") if source == "all" else (source,)
    bucket_cap = semantic_refresh_bucket_cap()
    bucket_counts: dict[str, int] = {}
    for source_kind in sources:
        index_path = source_index_path(out_root, source_kind)
        if not index_path.exists():
            continue
        for row in source_rows(index_path):
            if len(candidates) >= budget:
                return candidates
            source_chunk_id = str(row.get("source_chunk_id") or "")
            if not source_chunk_id or semantic_row_exists(conn, source_kind, source_chunk_id):
                continue
            bucket = semantic_candidate_bucket(source_kind, row)
            if bucket_counts.get(bucket, 0) >= bucket_cap:
                continue
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
            candidates.append({"source_kind": source_kind, **row})
    return candidates


def source_index_path(out_root: Path, source_kind: str) -> Path:
    if source_kind == "downloads":
        return out_root / "indexes" / "context.sqlite"
    if source_kind == "projects":
        return out_root / "indexes" / "projects.sqlite"
    if source_kind == "sessions":
        return out_root / "indexes" / "sessions.sqlite"
    return out_root / "indexes" / f"{source_kind}.sqlite"


def source_rows(index_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(index_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM chunks ORDER BY path, chunk_index")]
    finally:
        conn.close()


def semantic_row_exists(conn: sqlite3.Connection, source_kind: str, source_chunk_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM semantic_chunks WHERE source_kind = ? AND source_chunk_id = ? LIMIT 1",
        (source_kind, source_chunk_id),
    ).fetchone()
    return row is not None


def semantic_text(candidate: dict[str, Any], max_chars: int) -> str:
    return "\n".join(
        [
            str(candidate.get("path") or ""),
            str(candidate.get("relative_path") or ""),
            str(candidate.get("text") or "")[:max_chars],
        ]
    )


def semantic_row(candidate: dict[str, Any], embedding_json: str, backend: str, updated_at: str) -> tuple:
    try:
        source_metadata = json.loads(candidate.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        source_metadata = {}
    metadata = {
        key: candidate.get(key) if candidate.get(key) is not None else source_metadata.get(key)
        for key in (
            "doc_id",
            "chunk_id",
            "chunk_index",
            "source_id",
            "token_estimate",
            "project_id",
            "project_name",
            "project_path",
            "session_provider",
            "provider_source_id",
            "session_id",
            "thread_name",
            "cwd",
        )
        if candidate.get(key) is not None or source_metadata.get(key) is not None
    }
    return (
        candidate["source_kind"],
        candidate["source_chunk_id"],
        candidate.get("path"),
        candidate.get("relative_path"),
        candidate.get("text"),
        embedding_json,
        backend,
        "",
        updated_at,
        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    )


def write_meta(conn: sqlite3.Connection, backend: str) -> None:
    values = {
        "semantic_index_version": SEMANTIC_INDEX_VERSION,
        "embedding_backend": backend,
        "ann_backend": "none",
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        sorted(values.items()),
    )


def write_job(
    conn: sqlite3.Connection,
    job_id: str,
    source: str,
    backend: str,
    budget: int,
    created_at: str,
    status: str,
    processed: int,
    skipped: int,
    error: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO semantic_jobs (
          job_id, source, backend, budget, created_at, status, processed, skipped, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, source, backend, budget, created_at, status, processed, skipped, error),
    )


def semantic_refresh_result(
    db_path: Path,
    job_id: str,
    source: str,
    backend: str,
    budget: int,
    processed: int,
    skipped: int,
    status: str,
    error: str = "",
) -> dict[str, Any]:
    return {
        "semantic_index_version": SEMANTIC_INDEX_VERSION,
        "status": status,
        "job_id": job_id,
        "source": source,
        "backend": backend,
        "budget": budget,
        "processed": processed,
        "skipped": skipped,
        "error": error,
        "index_path": str(db_path),
    }
