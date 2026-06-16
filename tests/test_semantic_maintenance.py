from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_context.cli import main
from agent_context.cold_index import build_cold_index
from agent_context.io import write_jsonl
from agent_context.mcp_server import mcp_semantic_ann_prune, mcp_semantic_maintain
from agent_context.retrieval_backends import write_hnswlib_cache_metadata
from agent_context.semantic_index import (
    create_semantic_schema,
    semantic_ann_cache_paths,
    semantic_index_path_for,
    semantic_query_rows,
)
from agent_context.semantic_maintenance import run_semantic_ann_prune, run_semantic_maintenance


class FakeEmbeddingBackend:
    backend_id = "fastembed"
    dimensions = 3
    model_name = "fake"
    storage_format = "json_dense_float32"

    def embed_document(self, text: str) -> str:
        return json.dumps([1.0, 0.0, 0.0])

    def embed_documents(self, texts: list[str]) -> list[str]:
        return [self.embed_document(text) for text in texts]

    def score_rows(self, rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
        return {}


def write_sample_download_index(out: Path, chunk_count: int = 2) -> None:
    write_jsonl(
        out / "manifests" / "documents.jsonl",
        [
            {
                "doc_id": "doc-1",
                "path": "/tmp/a.md",
                "relative_path": "a.md",
                "scope": "/tmp",
                "sha256": "doc-1",
                "size_bytes": 20,
                "mtime": "2026-01-01T00:00:00+00:00",
                "extension": ".md",
                "mime": "text/markdown",
                "policy": "content",
                "parser": "direct_text",
                "parser_version": "test",
                "status": "ok",
                "extracted_md_path": "/tmp/a.md",
                "text_chars": 20,
                "chunk_count": chunk_count,
            }
        ],
    )
    write_jsonl(
        out / "manifests" / "chunks.jsonl",
        [
            {"doc_id": "doc-1", "chunk_id": f"doc-1:{index:04d}", "path": "/tmp/a.md", "chunk_index": index, "text": f"topic {index}"}
            for index in range(1, chunk_count + 1)
        ],
    )
    write_jsonl(out / "manifests" / "failures.jsonl", [])
    build_cold_index(out)


def test_semantic_maintenance_runs_until_noop_and_writes_reports(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    write_sample_download_index(out, chunk_count=2)
    monkeypatch.setattr("agent_context.semantic_index.get_embedding_backend", lambda _config: FakeEmbeddingBackend())

    result = run_semantic_maintenance(out, source="downloads", budget=1, max_jobs=3)

    assert result["status"] == "ok"
    assert result["stop_reason"] == "source_exhausted"
    assert result["jobs_run"] == 3
    assert result["processed"] == 2
    assert result["after"]["chunks"] == 2
    assert Path(result["report_json_path"]).exists()
    assert Path(result["report_md_path"]).exists()
    assert "Chunks: `0` -> `2`" in Path(result["report_md_path"]).read_text(encoding="utf-8")


def test_semantic_maintenance_interval_gate_skips_recent_job(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    write_sample_download_index(out, chunk_count=2)
    monkeypatch.setattr("agent_context.semantic_index.get_embedding_backend", lambda _config: FakeEmbeddingBackend())

    first = run_semantic_maintenance(out, source="downloads", budget=1, max_jobs=1)
    second = run_semantic_maintenance(out, source="downloads", budget=1, max_jobs=1, min_interval_minutes=60)

    assert first["processed"] == 1
    assert second["status"] == "skipped"
    assert second["stop_reason"] == "min_interval_not_elapsed"
    assert second["jobs_run"] == 0
    assert second["after"]["chunks"] == 1
    assert Path(second["report_json_path"]).exists()


def test_mcp_semantic_maintain(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    write_sample_download_index(out, chunk_count=1)
    monkeypatch.setattr("agent_context.semantic_index.get_embedding_backend", lambda _config: FakeEmbeddingBackend())

    result = mcp_semantic_maintain(out_root=str(out), source="downloads", budget=1, max_jobs=1)

    assert result["mcp_version"] == "0.1"
    assert result["status"] == "ok"
    assert result["processed"] == 1


def test_semantic_ann_prune_removes_stale_cache_and_keeps_active(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    write_sample_download_index(out, chunk_count=1)
    monkeypatch.setattr("agent_context.semantic_index.get_embedding_backend", lambda _config: FakeEmbeddingBackend())
    run_semantic_maintenance(out, source="downloads", budget=1, max_jobs=1)

    active_cache = active_download_ann_cache(out)
    active_cache.index_path.write_text("active", encoding="utf-8")
    write_hnswlib_cache_metadata(active_cache, 3, ["doc-1:0001"])
    stale_json = active_cache.index_path.parent / "hnswlib_stale.json"
    stale_bin = active_cache.index_path.parent / "hnswlib_stale.bin"
    stale_json.write_text(json.dumps({"fingerprint": "stale"}), encoding="utf-8")
    stale_bin.write_text("stale", encoding="utf-8")

    dry_run = run_semantic_ann_prune(out, dry_run=True)

    assert dry_run["files_removed"] == 2
    assert stale_json.exists()
    result = run_semantic_ann_prune(out)

    assert result["files_removed"] == 2
    assert not stale_json.exists()
    assert not stale_bin.exists()
    assert active_cache.index_path.exists()
    assert active_cache.metadata_path.exists()
    assert Path(result["report_json_path"]).exists()
    assert "Semantic ANN Cache Prune Report" in Path(result["report_md_path"]).read_text(encoding="utf-8")


def test_semantic_ann_prune_cli_and_mcp(tmp_path: Path, monkeypatch, capsys) -> None:
    out = tmp_path / "out"
    write_sample_download_index(out, chunk_count=1)
    monkeypatch.setattr("agent_context.semantic_index.get_embedding_backend", lambda _config: FakeEmbeddingBackend())
    run_semantic_maintenance(out, source="downloads", budget=1, max_jobs=1)
    cache = active_download_ann_cache(out)
    cache.index_path.write_text("active", encoding="utf-8")
    write_hnswlib_cache_metadata(cache, 3, ["doc-1:0001"])

    assert main(["semantic-ann-prune", "--out", str(out), "--max-entries", "8", "--dry-run"]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    mcp_result = mcp_semantic_ann_prune(out_root=str(out), max_entries=8, dry_run=True)

    assert cli_result["status"] == "ok"
    assert cli_result["dry_run"] is True
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["status"] == "ok"


def active_download_ann_cache(out: Path):
    conn = sqlite3.connect(semantic_index_path_for(out))
    conn.row_factory = sqlite3.Row
    try:
        create_semantic_schema(conn)
        rows = semantic_query_rows(conn, ["downloads"])
    finally:
        conn.close()
    cache = semantic_ann_cache_paths(out, rows, "fastembed", ["downloads"])
    assert cache is not None
    cache.index_path.parent.mkdir(parents=True, exist_ok=True)
    return cache
