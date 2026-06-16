from __future__ import annotations

import json
import sqlite3

from agent_context import cold_index


class RecordingBatchBackend:
    backend_id = "recording-batch"
    dimensions = 3
    model_name = None
    storage_format = "json_dense_float32"

    def __init__(self) -> None:
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    def embed_document(self, text: str) -> str:
        self.single_calls.append(text)
        return json.dumps(["single", text])

    def embed_documents(self, texts: list[str]) -> list[str]:
        self.batch_calls.append(list(texts))
        return [json.dumps(["batch", index]) for index, _ in enumerate(texts)]

    def score_rows(self, rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
        return {}


def test_insert_chunks_uses_batch_embedding_backend(monkeypatch) -> None:
    backend = RecordingBatchBackend()
    monkeypatch.setattr(cold_index, "get_embedding_backend", lambda config: backend)
    conn = sqlite3.connect(":memory:")
    documents = [
        {
            "doc_id": "doc-1",
            "path": "/tmp/source.md",
            "relative_path": "source.md",
            "chunk_count": 2,
        }
    ]
    chunks = [
        {
            "chunk_id": "chunk-1",
            "doc_id": "doc-1",
            "path": "/tmp/source.md",
            "chunk_index": 0,
            "text": "first chunk text",
        },
        {
            "chunk_id": "chunk-2",
            "doc_id": "doc-1",
            "path": "/tmp/source.md",
            "chunk_index": 1,
            "text": "second chunk text",
        },
    ]

    try:
        fts_enabled = cold_index.create_schema(conn)
        cold_index.insert_documents(conn, documents)
        cold_index.insert_chunks(conn, chunks, documents, fts_enabled)
        rows = conn.execute("SELECT chunk_id, embedding_json FROM chunks ORDER BY chunk_index").fetchall()
    finally:
        conn.close()

    assert backend.single_calls == []
    assert backend.batch_calls == [
        [
            "/tmp/source.md\nsource.md\nfirst chunk text",
            "/tmp/source.md\nsource.md\nsecond chunk text",
        ]
    ]
    assert rows == [
        ("chunk-1", json.dumps(["batch", 0])),
        ("chunk-2", json.dumps(["batch", 1])),
    ]


def test_insert_chunks_respects_embedding_batch_size_env(monkeypatch) -> None:
    backend = RecordingBatchBackend()
    monkeypatch.setenv("AGENT_CONTEXT_EMBED_BATCH_SIZE", "1")
    monkeypatch.setattr(cold_index, "get_embedding_backend", lambda config: backend)
    conn = sqlite3.connect(":memory:")
    documents = [
        {
            "doc_id": "doc-1",
            "path": "/tmp/source.md",
            "relative_path": "source.md",
            "chunk_count": 2,
        }
    ]
    chunks = [
        {"chunk_id": "chunk-1", "doc_id": "doc-1", "path": "/tmp/source.md", "chunk_index": 0, "text": "first"},
        {"chunk_id": "chunk-2", "doc_id": "doc-1", "path": "/tmp/source.md", "chunk_index": 1, "text": "second"},
    ]

    try:
        fts_enabled = cold_index.create_schema(conn)
        cold_index.insert_documents(conn, documents)
        cold_index.insert_chunks(conn, chunks, documents, fts_enabled)
        rows = conn.execute("SELECT chunk_id, embedding_json FROM chunks ORDER BY chunk_index").fetchall()
    finally:
        conn.close()

    assert len(backend.batch_calls) == 2
    assert [call[0] for call in backend.batch_calls] == [
        "/tmp/source.md\nsource.md\nfirst",
        "/tmp/source.md\nsource.md\nsecond",
    ]
    assert rows == [
        ("chunk-1", json.dumps(["batch", 0])),
        ("chunk-2", json.dumps(["batch", 0])),
    ]


def test_insert_chunks_skips_duplicate_source_chunk_ids(monkeypatch) -> None:
    backend = RecordingBatchBackend()
    monkeypatch.setattr(cold_index, "get_embedding_backend", lambda config: backend)
    conn = sqlite3.connect(":memory:")
    documents = [
        {
            "doc_id": "doc-1",
            "path": "/tmp/source.md",
            "relative_path": "source.md",
            "chunk_count": 2,
        }
    ]
    chunks = [
        {"chunk_id": "chunk-1", "doc_id": "doc-1", "path": "/tmp/source.md", "chunk_index": 0, "text": "first"},
        {"chunk_id": "chunk-1", "doc_id": "doc-1", "path": "/tmp/source.md", "chunk_index": 0, "text": "first duplicate"},
        {"chunk_id": "chunk-2", "doc_id": "doc-1", "path": "/tmp/source.md", "chunk_index": 1, "text": "second"},
    ]

    try:
        fts_enabled = cold_index.create_schema(conn)
        cold_index.insert_documents(conn, documents)
        cold_index.insert_chunks(conn, chunks, documents, fts_enabled)
        rows = conn.execute("SELECT chunk_id, text FROM chunks ORDER BY chunk_index").fetchall()
        fts_count = conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    finally:
        conn.close()

    assert rows == [("chunk-1", "first"), ("chunk-2", "second")]
    assert fts_count == 2
    assert len(backend.batch_calls[0]) == 2
