from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .evidence import (
    canonicalize_evidence_record,
    source_to_evidence_record,
    validate_evidence_record,
)
from .io import ensure_dir, read_jsonl


EVIDENCE_INDEX_VERSION = "0.1"

MANIFEST_SOURCES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("manifests/documents.jsonl", {"source_group": "downloads_documents", "type": "metadata_only"}),
    ("manifests/chunks.jsonl", {"source_group": "downloads_documents", "type": "chunk"}),
    ("manifests/projects.jsonl", {"source_group": "git_repositories", "provider": "git_project", "type": "project_provider"}),
    ("manifests/project_documents.jsonl", {"source_group": "git_repositories", "provider": "project_code_index"}),
    ("manifests/project_chunks.jsonl", {"source_group": "git_repositories", "provider": "project_code_index", "type": "project_code"}),
    ("manifests/symbols.jsonl", {"source_group": "git_repositories", "provider": "project_code_index", "type": "project_code"}),
    ("manifests/sessions.jsonl", {"source_group": "codex_sessions", "type": "session_provider"}),
    ("manifests/session_documents.jsonl", {"source_group": "codex_sessions", "type": "session_provider"}),
    ("manifests/session_chunks.jsonl", {"source_group": "codex_sessions", "type": "session_chunk"}),
    ("manifests/workflows.jsonl", {"source_group": "workflow_docs", "provider": "workflow_doc", "type": "workflow_doc"}),
    ("manifests/codebase_memory_sources.jsonl", {"source_group": "codebase_memory", "provider": "codebase_memory", "type": "codebase_memory"}),
    ("manifests/douyin_videos.jsonl", {"source_group": "media_profile", "provider": "douyin_video"}),
    ("manifests/douyin_authors.jsonl", {"source_group": "media_profile", "provider": "douyin_author"}),
    ("manifests/douyin_assets.jsonl", {"source_group": "media_profile", "provider": "douyin_asset"}),
)


def evidence_index_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "indexes" / "evidence.sqlite"


def build_evidence_index(out_root: Path) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    index_path = evidence_index_path_for(out_root)
    ensure_dir(index_path.parent)
    if index_path.exists():
        index_path.unlink()

    records = collect_evidence_records(out_root)
    conn = sqlite3.connect(index_path)
    try:
        fts_enabled = create_schema(conn)
        inserted = 0
        invalid = 0
        seen: dict[str, dict[str, Any]] = {}
        for record in records:
            errors = validate_evidence_record(record)
            if errors:
                invalid += 1
                continue
            evidence_id = str(record["evidence_id"])
            existing = seen.get(evidence_id)
            if existing and record_quality(existing) >= record_quality(record):
                continue
            seen[evidence_id] = record
        for record in seen.values():
            insert_evidence_record(conn, record, fts_enabled=fts_enabled)
            inserted += 1
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("evidence_index_version", EVIDENCE_INDEX_VERSION),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("fts_enabled", json.dumps(fts_enabled)),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "evidence_index_version": EVIDENCE_INDEX_VERSION,
        "status": "ok",
        "index_path": str(index_path),
        "records_indexed": inserted,
        "invalid_records": invalid,
    }


def create_schema(conn: sqlite3.Connection) -> bool:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE evidence_nodes (
            evidence_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_group TEXT NOT NULL,
            provider TEXT NOT NULL,
            path TEXT NOT NULL,
            relative_path TEXT,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            summary TEXT NOT NULL,
            quote TEXT NOT NULL,
            score REAL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX evidence_nodes_source_group_idx ON evidence_nodes(source_group)")
    conn.execute("CREATE INDEX evidence_nodes_source_type_idx ON evidence_nodes(source_type)")
    conn.execute("CREATE INDEX evidence_nodes_provider_idx ON evidence_nodes(provider)")
    conn.execute("CREATE INDEX evidence_nodes_path_idx ON evidence_nodes(path)")
    conn.execute(
        """
        CREATE TABLE evidence_edges (
            edge_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            weight REAL NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX evidence_edges_source_idx ON evidence_edges(source_id)")
    conn.execute("CREATE INDEX evidence_edges_target_idx ON evidence_edges(target_id)")
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE evidence_fts USING fts5(
                evidence_id UNINDEXED,
                title,
                text,
                summary,
                quote,
                path
            )
            """
        )
    except sqlite3.OperationalError:
        return False
    return True


def collect_evidence_records(out_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative_path, defaults in MANIFEST_SOURCES:
        records.extend(records_from_jsonl(out_root / relative_path, defaults=defaults))
    for sources_path in sorted((out_root / "packs").glob("*/sources.jsonl")):
        records.extend(records_from_jsonl(sources_path, defaults={}))
    for sources_path in sorted((out_root / "queries").glob("*/sources.jsonl")):
        records.extend(records_from_jsonl(sources_path, defaults={}))
    for attachments_path in sorted((out_root / "lab").glob("runs/*/attachments.jsonl")):
        records.extend(
            records_from_jsonl(
                attachments_path,
                defaults={"source_group": "lab_inputs", "provider": "doctor_lab"},
            )
        )
    return records


def records_from_jsonl(path: Path, *, defaults: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for source in read_jsonl(path):
        merged = {**defaults, **source}
        records.append(record_from_source(merged))
    return records


def record_from_source(source: dict[str, Any]) -> dict[str, Any]:
    existing = source.get("evidence")
    if isinstance(existing, dict):
        return canonicalize_evidence_record(existing)
    return source_to_evidence_record(source)


def record_quality(record: dict[str, Any]) -> tuple[int, float]:
    text_size = len(str(record.get("text") or "")) + len(str(record.get("summary") or "")) + len(str(record.get("quote") or ""))
    try:
        score = float(record.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return (text_size, score)


def insert_evidence_record(conn: sqlite3.Connection, record: dict[str, Any], *, fts_enabled: bool) -> None:
    payload_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
    conn.execute(
        """
        INSERT OR REPLACE INTO evidence_nodes(
            evidence_id,
            source_type,
            source_group,
            provider,
            path,
            relative_path,
            title,
            text,
            summary,
            quote,
            score,
            payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["evidence_id"],
            record["source_type"],
            record["source_group"],
            record["provider"],
            record["path"],
            record["relative_path"],
            record["title"],
            record["text"],
            record["summary"],
            record["quote"],
            numeric_score(record.get("score")),
            payload_json,
        ),
    )
    if fts_enabled:
        conn.execute(
            "INSERT INTO evidence_fts(evidence_id, title, text, summary, quote, path) VALUES (?, ?, ?, ?, ?, ?)",
            (
                record["evidence_id"],
                record["title"],
                record["text"],
                record["summary"],
                record["quote"],
                record["path"],
            ),
        )
    for edge in evidence_edges_for(record):
        conn.execute(
            """
            INSERT OR REPLACE INTO evidence_edges(edge_id, source_id, edge_type, target_id, weight, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                edge["edge_id"],
                edge["source_id"],
                edge["edge_type"],
                edge["target_id"],
                edge["weight"],
                json.dumps(edge["payload"], ensure_ascii=False, sort_keys=True),
            ),
        )


def numeric_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evidence_edges_for(record: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_id = str(record["evidence_id"])
    edges = []
    for edge_type, target in (
        ("belongs_to_source_group", record.get("source_group")),
        ("belongs_to_provider", record.get("provider")),
        ("has_source_type", record.get("source_type")),
    ):
        if target:
            edges.append(make_edge(evidence_id, edge_type, str(target), 1.0, {"derived": True}))
    path = record.get("path")
    if path:
        edges.append(make_edge(evidence_id, "has_path", f"path:{path}", 1.0, {"derived": True}))
    for raw_edge in record.get("edges") or []:
        if not isinstance(raw_edge, dict):
            continue
        target = raw_edge.get("target_id") or raw_edge.get("target") or raw_edge.get("id")
        if not target:
            continue
        edges.append(
            make_edge(
                evidence_id,
                str(raw_edge.get("type") or raw_edge.get("edge_type") or "related_to"),
                str(target),
                numeric_score(raw_edge.get("weight")) or 1.0,
                raw_edge,
            )
        )
    return edges


def make_edge(source_id: str, edge_type: str, target_id: str, weight: float, payload: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(f"{source_id}|{edge_type}|{target_id}".encode("utf-8")).hexdigest()[:24]
    return {
        "edge_id": f"edge:{digest}",
        "source_id": source_id,
        "edge_type": edge_type,
        "target_id": target_id,
        "weight": weight,
        "payload": payload,
    }


def search_evidence_index(out_root: Path, query: str, *, limit: int = 12) -> dict[str, Any]:
    index_path = evidence_index_path_for(out_root)
    if not index_path.exists():
        return {
            "evidence_index_version": EVIDENCE_INDEX_VERSION,
            "status": "missing",
            "query": query,
            "index_path": str(index_path),
            "sources": [],
        }
    terms = query_terms(query)
    conn = sqlite3.connect(index_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = candidate_rows(conn, terms, limit=max(100, limit * 20))
        sources = rank_rows(rows, terms, limit=max(1, limit))
        total_records = conn.execute("SELECT count(*) FROM evidence_nodes").fetchone()[0]
        fts_enabled = meta_bool(conn, "fts_enabled")
    finally:
        conn.close()
    return {
        "evidence_index_version": EVIDENCE_INDEX_VERSION,
        "status": "ok",
        "query": query,
        "index_path": str(index_path),
        "records_searched": total_records,
        "fts_enabled": fts_enabled,
        "sources": sources,
    }


def candidate_rows(conn: sqlite3.Connection, terms: list[str], *, limit: int) -> list[sqlite3.Row]:
    if not terms:
        return list(
            conn.execute(
                "SELECT * FROM evidence_nodes ORDER BY coalesce(score, 0) DESC, evidence_id ASC LIMIT ?",
                (limit,),
            )
        )
    haystack = "lower(title || char(10) || text || char(10) || summary || char(10) || quote || char(10) || path)"
    clauses = " OR ".join([f"{haystack} LIKE ?" for _ in terms])
    params = [f"%{term.lower()}%" for term in terms]
    return list(
        conn.execute(
            f"SELECT * FROM evidence_nodes WHERE {clauses} ORDER BY coalesce(score, 0) DESC, evidence_id ASC LIMIT ?",
            (*params, limit),
        )
    )


def rank_rows(rows: Iterable[sqlite3.Row], terms: list[str], *, limit: int) -> list[dict[str, Any]]:
    ranked = []
    denominator = max(1, len(terms))
    for row in rows:
        payload = json.loads(row["payload_json"])
        haystack = " ".join(
            str(row[field] or "")
            for field in ("title", "text", "summary", "quote", "path")
        ).lower()
        matched_terms = [term for term in terms if term.lower() in haystack]
        lexical = len(matched_terms) / denominator
        path_match = 1.0 if any(term.lower() in str(row["path"] or "").lower() for term in terms) else 0.0
        score = numeric_score(row["score"]) or 0.0
        evidence_score = round(min(1.0, lexical * 0.75 + min(score, 1.0) * 0.15 + path_match * 0.10), 6)
        source = {
            "type": "evidence",
            "source_id": row["evidence_id"],
            "source_group": row["source_group"],
            "provider": row["provider"],
            "source_type": row["source_type"],
            "path": row["path"],
            "relative_path": row["relative_path"],
            "title": row["title"],
            "score": evidence_score,
            "score_parts": {
                "evidence_lexical": round(lexical, 6),
                "evidence_source_score": round(min(score, 1.0), 6),
                "evidence_path_match": path_match,
            },
            "matched_terms": matched_terms,
            "snippet": row["quote"] or row["summary"] or row["text"],
            "evidence": payload,
        }
        ranked.append(source)
    ranked.sort(key=lambda source: (-float(source["score"]), str(source["source_group"]), str(source["path"])))
    return ranked[:limit]


def query_terms(query: str) -> list[str]:
    terms = re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]+", query.lower())
    stop = {"the", "and", "for", "with", "from", "this", "that", "一个", "哪些", "如何", "怎么"}
    return list(dict.fromkeys(term for term in terms if len(term) >= 2 and term not in stop))[:16]


def meta_bool(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if not row:
        return False
    try:
        return bool(json.loads(row[0]))
    except json.JSONDecodeError:
        return False
