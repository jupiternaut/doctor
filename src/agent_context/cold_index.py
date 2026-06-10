from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .ingest import IngestPaths
from .io import ensure_dir, read_jsonl, write_jsonl, write_text
from .pack import slugify, snippet


INDEX_VERSION = "0.2"
EMBEDDING_DIMENSIONS = 384
DEFAULT_QUERY_LIMIT = 12
STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "哪些",
    "文件",
    "适合",
    "进入",
    "个人",
    "什么",
    "如何",
    "怎么",
}
QUERY_EXPANSIONS = {
    "个人助手": ["agent", "assistant", "mcp", "skill", "workflow", "context", "助手"],
    "长期记忆": ["memory", "remember", "skill", "workflow", "context", "handoff", "permalink", "记忆", "沉淀", "上下文", "工作流"],
    "开源往事": ["开源", "黑盒", "gnu", "linux", "unix", "gpl", "微软", "sun", "ibm", "自由软件", "闭源"],
    "开源": ["gnu", "linux", "unix", "gpl", "微软", "sun", "ibm", "自由软件", "闭源", "开放源代码"],
    "热上下文": ["context", "pack", "handoff", "上下文"],
    "冷索引": ["index", "search", "retrieval", "rag", "sqlite"],
    "rag": ["retrieval", "index", "search", "context"],
}


def index_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "indexes" / "context.sqlite"


def build_cold_index(out_root: Path) -> dict:
    out_root = out_root.expanduser().resolve()
    paths = IngestPaths.from_root(out_root)
    documents = read_jsonl(paths.documents_jsonl)
    chunks = read_jsonl(paths.chunks_jsonl)
    failures = read_jsonl(paths.failures_jsonl)

    if not documents and not chunks:
        raise FileNotFoundError(f"no manifests found under {paths.manifests}")

    db_path = index_path_for(out_root)
    reset_sqlite_database(db_path)
    ensure_dir(db_path.parent)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        fts_enabled = create_schema(conn)
        insert_documents(conn, documents)
        insert_chunks(conn, chunks, documents, fts_enabled)
        insert_failures(conn, failures)
        write_meta(
            conn,
            {
                "index_version": INDEX_VERSION,
                "built_at": datetime.now().astimezone().isoformat(),
                "documents": str(len(documents)),
                "chunks": str(len(chunks)),
                "failures": str(len(failures)),
                "fts_enabled": "true" if fts_enabled else "false",
                "embedding": f"local-hash-vector-lite-{EMBEDDING_DIMENSIONS}",
            },
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "index_version": INDEX_VERSION,
        "index_path": str(db_path),
        "documents": len(documents),
        "chunks": len(chunks),
        "failures": len(failures),
        "fts_enabled": fts_enabled,
        "embedding": f"local-hash-vector-lite-{EMBEDDING_DIMENSIONS}",
    }


def reset_sqlite_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


def source_id_for(record: dict) -> str:
    payload = "|".join(
        [
            str(record.get("doc_id") or record.get("sha256") or ""),
            str(record.get("path") or ""),
        ]
    )
    return "source:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_chunk_id_for(source_id: str, chunk: dict) -> str:
    payload = "|".join(
        [
            source_id,
            str(chunk.get("chunk_id") or ""),
            str(chunk.get("chunk_index") or ""),
            str(chunk.get("path") or ""),
        ]
    )
    return "source-chunk:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_schema(conn: sqlite3.Connection) -> bool:
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE documents (
          source_id TEXT PRIMARY KEY,
          doc_id TEXT NOT NULL,
          path TEXT NOT NULL,
          relative_path TEXT,
          scope TEXT,
          sha256 TEXT,
          size_bytes INTEGER,
          mtime TEXT,
          extension TEXT,
          mime TEXT,
          policy TEXT,
          parser TEXT,
          parser_version TEXT,
          status TEXT,
          extracted_md_path TEXT,
          text_chars INTEGER DEFAULT 0,
          chunk_count INTEGER DEFAULT 0,
          metadata_json TEXT NOT NULL
        );

        CREATE INDEX idx_documents_doc_id ON documents(doc_id);
        CREATE INDEX idx_documents_path ON documents(path);
        CREATE INDEX idx_documents_relative_path ON documents(relative_path);
        CREATE INDEX idx_documents_extension ON documents(extension);
        CREATE INDEX idx_documents_policy ON documents(policy);
        CREATE INDEX idx_documents_status ON documents(status);

        CREATE TABLE chunks (
          source_chunk_id TEXT PRIMARY KEY,
          chunk_id TEXT NOT NULL,
          source_id TEXT NOT NULL,
          doc_id TEXT NOT NULL,
          path TEXT NOT NULL,
          relative_path TEXT,
          chunk_index INTEGER NOT NULL,
          char_start INTEGER,
          char_end INTEGER,
          token_estimate INTEGER,
          text TEXT NOT NULL,
          embedding_json TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          FOREIGN KEY(source_id) REFERENCES documents(source_id)
        );

        CREATE INDEX idx_chunks_chunk_id ON chunks(chunk_id);
        CREATE INDEX idx_chunks_source_id ON chunks(source_id);
        CREATE INDEX idx_chunks_doc_id ON chunks(doc_id);
        CREATE INDEX idx_chunks_path ON chunks(path);
        CREATE INDEX idx_chunks_relative_path ON chunks(relative_path);

        CREATE TABLE failures (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL,
          sha256 TEXT,
          stage TEXT,
          parser TEXT,
          error_type TEXT,
          error TEXT,
          recoverable INTEGER,
          metadata_json TEXT NOT NULL
        );

        CREATE INDEX idx_failures_path ON failures(path);
        CREATE INDEX idx_failures_parser ON failures(parser);
        """
    )
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
              chunk_id UNINDEXED,
              doc_id UNINDEXED,
              path,
              relative_path,
              text,
              tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )
        return True
    except sqlite3.OperationalError:
        return False


def insert_documents(conn: sqlite3.Connection, documents: list[dict]) -> None:
    rows = [
        (
            source_id_for(record),
            record.get("doc_id"),
            record.get("path"),
            record.get("relative_path"),
            record.get("scope"),
            record.get("sha256"),
            record.get("size_bytes"),
            record.get("mtime"),
            record.get("extension"),
            record.get("mime"),
            record.get("policy"),
            record.get("parser"),
            record.get("parser_version"),
            record.get("status"),
            record.get("extracted_md_path"),
            record.get("text_chars", 0),
            record.get("chunk_count", 0),
            json.dumps(record, ensure_ascii=False, sort_keys=True),
        )
        for record in documents
    ]
    conn.executemany(
        """
        INSERT INTO documents (
          source_id, doc_id, path, relative_path, scope, sha256, size_bytes, mtime,
          extension, mime, policy, parser, parser_version, status,
          extracted_md_path, text_chars, chunk_count, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_chunks(conn: sqlite3.Connection, chunks: list[dict], documents: list[dict], fts_enabled: bool) -> None:
    documents_by_path = {record["path"]: record for record in documents}
    documents_by_id: dict[str, dict] = {}
    for record in documents:
        documents_by_id.setdefault(record["doc_id"], record)
    rows = []
    fts_rows = []
    for record in chunks:
        doc = documents_by_path.get(record.get("path")) or documents_by_id.get(record["doc_id"], {})
        source_id = source_id_for(doc) if doc else source_id_for({"doc_id": record.get("doc_id"), "path": record.get("path")})
        source_chunk_id = source_chunk_id_for(source_id, record)
        relative_path = doc.get("relative_path")
        embedding_text = "\n".join([record.get("path", ""), relative_path or "", record.get("text", "")])
        rows.append(
            (
                source_chunk_id,
                record.get("chunk_id"),
                source_id,
                record.get("doc_id"),
                record.get("path"),
                relative_path,
                record.get("chunk_index"),
                record.get("char_start"),
                record.get("char_end"),
                record.get("token_estimate"),
                record.get("text"),
                encode_vector(vector_for(embedding_text)),
                json.dumps(record, ensure_ascii=False, sort_keys=True),
            )
        )
        fts_rows.append(
            (
                source_chunk_id,
                record.get("doc_id"),
                record.get("path"),
                relative_path or "",
                record.get("text", ""),
            )
        )

    conn.executemany(
        """
        INSERT INTO chunks (
          source_chunk_id, chunk_id, source_id, doc_id, path, relative_path, chunk_index, char_start,
          char_end, token_estimate, text, embedding_json, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    if fts_enabled:
        conn.executemany(
            "INSERT INTO chunks_fts (chunk_id, doc_id, path, relative_path, text) VALUES (?, ?, ?, ?, ?)",
            fts_rows,
        )


def insert_failures(conn: sqlite3.Connection, failures: list[dict]) -> None:
    rows = [
        (
            record.get("path"),
            record.get("sha256"),
            record.get("stage"),
            record.get("parser"),
            record.get("error_type"),
            record.get("error"),
            1 if record.get("recoverable") else 0,
            json.dumps(record, ensure_ascii=False, sort_keys=True),
        )
        for record in failures
    ]
    conn.executemany(
        """
        INSERT INTO failures (
          path, sha256, stage, parser, error_type, error, recoverable, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def write_meta(conn: sqlite3.Connection, values: dict[str, str]) -> None:
    conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(values.items()))


def query_cold_index(out_root: Path, query: str, *, limit: int = DEFAULT_QUERY_LIMIT) -> dict:
    out_root = out_root.expanduser().resolve()
    db_path = index_path_for(out_root)
    if not db_path.exists():
        build_cold_index(out_root)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        meta = read_meta(conn)
        candidates = retrieve_candidates(conn, query, max(1, limit), meta)
        sources = render_sources(candidates[:limit])
    finally:
        conn.close()

    created_at = datetime.now().astimezone().isoformat()
    query_id = f"{slugify(query)}-rag-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    query_dir = ensure_dir(out_root / "queries" / query_id)
    sources_path = query_dir / "sources.jsonl"
    manifest_path = query_dir / "manifest.json"
    context_path = query_dir / "context.md"

    manifest = {
        "rag_version": INDEX_VERSION,
        "query_id": query_id,
        "query": query,
        "created_at": created_at,
        "index_path": str(db_path),
        "retrieval_mode": "hybrid_fts_vector_lite_path",
        "limit": limit,
        "sources_included": len(sources),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "index_meta": meta,
    }

    write_jsonl(sources_path, sources)
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(context_path, render_query_context(query, created_at, db_path, meta, sources))

    return {
        "query_id": query_id,
        "query": query,
        "index_path": str(db_path),
        "context_md_path": str(context_path),
        "sources_jsonl_path": str(sources_path),
        "manifest_json_path": str(manifest_path),
        "sources_included": len(sources),
    }


def read_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}


def retrieve_candidates(conn: sqlite3.Connection, query: str, limit: int, meta: dict[str, str]) -> list[dict]:
    chunk_rows = {row["source_chunk_id"]: dict(row) for row in conn.execute("SELECT * FROM chunks")}
    document_rows = {row["source_id"]: dict(row) for row in conn.execute("SELECT * FROM documents")}
    candidates: dict[str, dict] = {}

    for source_chunk_id, score in fts_scores(conn, query, limit * 8, meta).items():
        if source_chunk_id in chunk_rows:
            candidate = candidate_for_chunk(chunk_rows[source_chunk_id], document_rows)
            candidate["score_parts"]["fts"] = score
            candidates[source_chunk_id] = candidate

    for source_chunk_id, score in vector_scores(chunk_rows, query, limit * 8).items():
        candidate = candidates.get(source_chunk_id) or candidate_for_chunk(chunk_rows[source_chunk_id], document_rows)
        candidate["score_parts"]["vector"] = score
        candidates[source_chunk_id] = candidate

    for result_id, score in path_scores(chunk_rows, document_rows, query, limit * 8).items():
        if result_id.startswith("chunk:"):
            source_chunk_id = result_id.removeprefix("chunk:")
            candidate = candidates.get(source_chunk_id) or candidate_for_chunk(chunk_rows[source_chunk_id], document_rows)
            candidate["score_parts"]["path"] = score
            candidates[source_chunk_id] = candidate
        else:
            source_id = result_id.removeprefix("doc:")
            candidate = candidate_for_document(document_rows[source_id])
            candidate["score_parts"]["path"] = score
            candidates[result_id] = candidate

    ranked = []
    for candidate in candidates.values():
        parts = candidate["score_parts"]
        asset_prior = agent_asset_prior(candidate, query)
        if asset_prior:
            parts["asset"] = asset_prior
        topic_prior = topical_prior(candidate, query)
        if topic_prior:
            parts["topic"] = topic_prior
        candidate["score"] = round(
            parts.get("fts", 0.0) * 0.35
            + parts.get("vector", 0.0) * 0.25
            + parts.get("path", 0.0) * 0.10
            + parts.get("asset", 0.0) * 0.20
            + parts.get("topic", 0.0) * 0.10,
            6,
        )
        ranked.append(candidate)

    ranked.sort(key=lambda item: (-item["score"], item.get("path") or "", item.get("chunk_index") or 0))
    return diversify_by_source(ranked)


def diversify_by_source(ranked: list[dict]) -> list[dict]:
    primary = []
    overflow = []
    seen_sources = set()
    for candidate in ranked:
        source_key = candidate.get("source_id") or candidate.get("doc_id") or candidate.get("path")
        if source_key in seen_sources:
            overflow.append(candidate)
            continue
        seen_sources.add(source_key)
        primary.append(candidate)
    return primary + overflow


def agent_asset_prior(candidate: dict, query: str) -> float:
    lower_query = query.lower()
    if not any(trigger in lower_query for trigger in ("长期记忆", "个人助手", "agent", "assistant", "memory")):
        return 0.0

    path = (candidate.get("path") or "").lower()
    relative_path = (candidate.get("relative_path") or "").lower()
    extension = (candidate.get("extension") or "").lower()
    haystack = f"{path} {relative_path}"

    score = 0.0
    if extension in {".skill", ".md", ".markdown"}:
        score += 0.25
    if "skill.md" in haystack or haystack.endswith("/skill.md"):
        score += 0.75
    for marker in ("skill", "workflow", "context", "memory", "handoff", "mcp", "agent", "task-planner", "vibe"):
        if marker in haystack:
            score += 0.2
    for marker in ("工作流", "上下文", "记忆", "沉淀", "助手", "任务规划"):
        if marker in haystack:
            score += 0.2
    return min(score, 1.0)


def topical_prior(candidate: dict, query: str) -> float:
    lower_query = query.lower()
    if not any(trigger in lower_query for trigger in ("开源往事", "开源", "open source", "opensource")):
        return 0.0

    path = (candidate.get("path") or "").lower()
    relative_path = (candidate.get("relative_path") or "").lower()
    text = (candidate.get("text") or "").lower()
    haystack = f"{path} {relative_path} {text[:4000]}"
    markers = (
        "开源",
        "黑盒",
        "gnu",
        "linux",
        "unix",
        "gpl",
        "微软",
        "sun",
        "ibm",
        "自由软件",
        "闭源",
        "开放源代码",
        "stallman",
        "richard stallman",
    )
    hits = sum(1 for marker in markers if marker_matches(marker, haystack))
    return min(1.0, hits / 4.0)


def marker_matches(marker: str, haystack: str) -> bool:
    if re.fullmatch(r"[a-z0-9 ]+", marker):
        pattern = r"(?<![a-z0-9])" + re.escape(marker) + r"(?![a-z0-9])"
        return re.search(pattern, haystack) is not None
    return marker in haystack


def fts_scores(conn: sqlite3.Connection, query: str, limit: int, meta: dict[str, str]) -> dict[str, float]:
    if meta.get("fts_enabled") != "true":
        return {}
    expression = fts_expression(query)
    if not expression:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT chunk_id, bm25(chunks_fts) AS rank
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (expression, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    if not rows:
        return {}
    denominator = max(len(rows), 1)
    return {row["chunk_id"]: (denominator - index) / denominator for index, row in enumerate(rows)}


def vector_scores(chunk_rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
    query_vector = query_vector_for(query)
    if not query_vector:
        return {}
    terms = set(query_terms(query))
    scored = []
    for source_chunk_id, row in chunk_rows.items():
        haystack = " ".join([row.get("path") or "", row.get("relative_path") or "", row.get("text") or ""]).lower()
        overlap = sum(1 for term in terms if term in haystack)
        if overlap == 0:
            continue
        score = cosine(query_vector, decode_vector(row["embedding_json"]))
        if score > 0:
            scored.append((score * min(1.0, overlap / 3.0), source_chunk_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    top = scored[:limit]
    if not top:
        return {}
    max_score = max(score for score, _ in top) or 1.0
    return {source_chunk_id: score / max_score for score, source_chunk_id in top}


def path_scores(chunk_rows: dict[str, dict], document_rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
    terms = set(query_terms(query))
    if not terms:
        return {}

    scored: list[tuple[float, str]] = []
    for source_chunk_id, row in chunk_rows.items():
        haystack = " ".join([row.get("path") or "", row.get("relative_path") or ""]).lower()
        overlap = sum(1 for term in terms if term in haystack)
        if overlap:
            scored.append((float(overlap), f"chunk:{source_chunk_id}"))

    for source_id, row in document_rows.items():
        if int(row.get("chunk_count") or 0) > 0:
            continue
        haystack = " ".join(
            [
                row.get("path") or "",
                row.get("relative_path") or "",
                row.get("extension") or "",
                row.get("policy") or "",
                row.get("parser") or "",
            ]
        ).lower()
        overlap = sum(1 for term in terms if term in haystack)
        if overlap:
            score = float(overlap)
            if row.get("policy") == "metadata_only":
                score += 0.25
            scored.append((score, f"doc:{source_id}"))

    scored.sort(key=lambda item: (-item[0], item[1]))
    top = scored[:limit]
    if not top:
        return {}
    max_score = max(score for score, _ in top) or 1.0
    return {result_id: score / max_score for score, result_id in top}


def candidate_for_chunk(chunk: dict, documents_by_id: dict[str, dict]) -> dict:
    doc = documents_by_id.get(chunk["source_id"], {})
    return {
        "type": "chunk",
        "source_chunk_id": chunk["source_chunk_id"],
        "source_id": chunk["source_id"],
        "chunk_id": chunk["chunk_id"],
        "doc_id": chunk["doc_id"],
        "path": chunk["path"],
        "relative_path": chunk.get("relative_path"),
        "chunk_index": chunk.get("chunk_index"),
        "parser": doc.get("parser"),
        "policy": doc.get("policy"),
        "status": doc.get("status"),
        "extension": doc.get("extension"),
        "text": chunk.get("text", ""),
        "score_parts": {},
    }


def candidate_for_document(doc: dict) -> dict:
    return {
        "type": "metadata_only",
        "source_id": doc["source_id"],
        "doc_id": doc["doc_id"],
        "path": doc["path"],
        "relative_path": doc.get("relative_path"),
        "parser": doc.get("parser"),
        "policy": doc.get("policy"),
        "status": doc.get("status"),
        "extension": doc.get("extension"),
        "text": metadata_text(doc),
        "score_parts": {},
    }


def render_sources(candidates: list[dict]) -> list[dict]:
    sources = []
    for candidate in candidates:
        source = {
            "type": candidate["type"],
            "score": candidate["score"],
            "score_parts": {key: round(value, 6) for key, value in sorted(candidate["score_parts"].items())},
            "doc_id": candidate["doc_id"],
            "source_id": candidate.get("source_id"),
            "path": candidate["path"],
            "relative_path": candidate.get("relative_path"),
            "parser": candidate.get("parser"),
            "policy": candidate.get("policy"),
            "status": candidate.get("status"),
            "extension": candidate.get("extension"),
            "snippet": snippet(candidate.get("text") or metadata_text(candidate), 520),
        }
        if candidate["type"] == "chunk":
            source["source_chunk_id"] = candidate["source_chunk_id"]
            source["chunk_id"] = candidate["chunk_id"]
            source["chunk_index"] = candidate.get("chunk_index")
        sources.append(source)
    return sources


def render_query_context(query: str, created_at: str, db_path: Path, meta: dict[str, str], sources: list[dict]) -> str:
    lines = [
        "---",
        f"rag_version: {INDEX_VERSION}",
        f"query: {query}",
        f"created_at: {created_at}",
        f"index_path: {db_path}",
        "retrieval_mode: hybrid_fts_vector_lite_path",
        "---",
        "",
        "# RAG Query",
        "",
        query,
        "",
        "# Retrieval Summary",
        "",
        f"- Index: `{db_path}`",
        f"- Documents indexed: {meta.get('documents', '0')}",
        f"- Chunks indexed: {meta.get('chunks', '0')}",
        f"- Failures indexed: {meta.get('failures', '0')}",
        f"- FTS enabled: {meta.get('fts_enabled', 'false')}",
        f"- Embedding: `{meta.get('embedding', 'unknown')}`",
        f"- Sources returned: {len(sources)}",
        "",
        "# Top Sources",
        "",
    ]

    if sources:
        for source in sources:
            lines.append(
                f"- `{source['path']}` ({source['type']}, score={source['score']}, parts={source['score_parts']})"
            )
    else:
        lines.append("- No matching sources found.")

    lines.extend(["", "# Snippets", ""])
    for source in sources[:8]:
        lines.append(f"> {source['snippet']}")
        lines.append(">")
        lines.append(f"> Source: `{source['path']}`")
        lines.append("")

    lines.extend(["# Limits", ""])
    lines.append("- v0.2 uses local SQLite FTS plus deterministic hash-vector-lite retrieval.")
    lines.append("- This is a real cold query index, but not yet neural embeddings or ANN vector search.")
    lines.append("- Metadata-only files can be retrieved by path/type but still need extraction, OCR, or transcription for content search.")
    lines.append("- Query output is a RAG context pack; Codex/MCP automatic lookup is still a separate integration step.")
    lines.append("")
    return "\n".join(lines)


def metadata_text(doc: dict) -> str:
    return (
        f"metadata-only source; path={doc.get('path')}; relative_path={doc.get('relative_path')}; "
        f"extension={doc.get('extension')}; size_bytes={doc.get('size_bytes')}; parser={doc.get('parser')}; "
        f"policy={doc.get('policy')}; status={doc.get('status')}"
    )


def fts_expression(query: str) -> str:
    terms = query_terms(query)
    safe_terms = []
    for term in terms[:12]:
        cleaned = term.replace('"', '""')
        if cleaned:
            safe_terms.append(f'"{cleaned}"')
    return " OR ".join(safe_terms)


def lexical_terms(text: str) -> list[str]:
    lower = text.lower()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", lower):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            terms.append(token)
            terms.extend(cjk_ngrams(token, 2))
            terms.extend(cjk_ngrams(token, 3))
        else:
            for part in token.split("_"):
                if len(part) >= 2 and part not in STOP_TERMS:
                    terms.append(part)
    deduped = []
    seen = set()
    for term in terms:
        if term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def query_terms(text: str) -> list[str]:
    terms = lexical_terms(text)
    lower = text.lower()
    for trigger, expansions in QUERY_EXPANSIONS.items():
        if trigger.lower() in lower:
            terms.extend(expansions)
    deduped = []
    seen = set()
    for term in terms:
        if term not in STOP_TERMS and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def cjk_ngrams(token: str, size: int) -> list[str]:
    if len(token) < size:
        return []
    return [token[index : index + size] for index in range(0, len(token) - size + 1)]


def vector_for(text: str) -> dict[int, float]:
    values: dict[int, float] = {}
    for term in lexical_terms(text):
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        values[index] = values.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in values.values()))
    if norm == 0:
        return {}
    return {index: value / norm for index, value in values.items()}


def query_vector_for(text: str) -> dict[int, float]:
    return vector_from_terms(query_terms(text))


def vector_from_terms(terms: list[str]) -> dict[int, float]:
    values: dict[int, float] = {}
    for term in terms:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        values[index] = values.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in values.values()))
    if norm == 0:
        return {}
    return {index: value / norm for index, value in values.items()}


def encode_vector(vector: dict[int, float]) -> str:
    pairs = [[index, round(value, 8)] for index, value in sorted(vector.items())]
    return json.dumps(pairs, separators=(",", ":"))


def decode_vector(payload: str) -> dict[int, float]:
    return {int(index): float(value) for index, value in json.loads(payload or "[]")}


def cosine(left: dict[int, float], right: dict[int, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())
