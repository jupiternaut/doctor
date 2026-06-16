from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from agent_context.cli import main as cli_main
from agent_context.mcp_server import mcp_semantic_benchmark


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "benchmark_embedding_backends.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("benchmark_embedding_backends", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def document(path: str, *, doc_id: str = "doc-1") -> dict:
    return {
        "chunk_count": 1,
        "doc_id": doc_id,
        "extension": ".md",
        "extracted_md_path": path,
        "mime": "text/markdown",
        "mtime": "2026-01-01T00:00:00+00:00",
        "parser": "markdown",
        "parser_version": "test",
        "path": path,
        "policy": "content",
        "relative_path": Path(path).name,
        "scope": "test",
        "sha256": doc_id,
        "size_bytes": 100,
        "status": "ok",
        "text_chars": 100,
    }


def chunk(path: str, text: str, *, doc_id: str = "doc-1") -> dict:
    return {
        "char_end": len(text),
        "char_start": 0,
        "chunk_id": f"{doc_id}:0001",
        "chunk_index": 1,
        "doc_id": doc_id,
        "path": path,
        "text": text,
        "token_estimate": 20,
    }


def test_downloads_benchmark_writes_report_without_main_index(tmp_path: Path) -> None:
    module = load_script_module()
    out = tmp_path / "out"
    path = str(tmp_path / "source" / "notes.md")
    write_jsonl(out / "manifests" / "documents.jsonl", [document(path)])
    source_chunk = chunk(path, "personal assistant memory retrieval")
    write_jsonl(out / "manifests" / "chunks.jsonl", [source_chunk, source_chunk])
    write_jsonl(out / "manifests" / "failures.jsonl", [])

    assert module.main(["--out", str(out), "--source", "downloads", "--query", "assistant memory"]) == 0

    reports = sorted((out / "reports").glob("embedding_backend_benchmark_downloads_*.md"))
    assert reports
    report_text = reports[-1].read_text(encoding="utf-8")
    assert "hash-vector-lite" in report_text
    assert "fastembed-rerank" in report_text
    assert "FastEmbed rerank policy" in report_text
    assert "| `manifests/chunks.jsonl` | 2 | 1 |" in report_text
    assert "personal assistant memory retrieval" not in report_text
    assert not (out / "indexes" / "context.sqlite").exists()


def test_projects_benchmark_writes_report_without_main_index(tmp_path: Path) -> None:
    module = load_script_module()
    out = tmp_path / "out"
    path = str(tmp_path / "project" / "README.md")
    write_jsonl(out / "manifests" / "project_documents.jsonl", [document(path, doc_id="project-doc-1")])
    write_jsonl(
        out / "manifests" / "project_chunks.jsonl",
        [chunk(path, "ranking backend source code notes", doc_id="project-doc-1")],
    )
    write_jsonl(out / "manifests" / "project_failures.jsonl", [])

    assert module.main(["--out", str(out), "--source", "projects", "--query", "backend ranking"]) == 0

    reports = sorted((out / "reports").glob("embedding_backend_benchmark_projects_*.md"))
    assert reports
    report_text = reports[-1].read_text(encoding="utf-8")
    assert "project_documents.jsonl" in report_text
    assert "fastembed-rerank" in report_text
    assert "FastEmbed rerank policy" in report_text
    assert not (out / "indexes" / "projects.sqlite").exists()


def test_semantic_benchmark_cli_returns_report_path(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    path = str(tmp_path / "project" / "README.md")
    write_jsonl(out / "manifests" / "project_documents.jsonl", [document(path, doc_id="project-doc-1")])
    write_jsonl(
        out / "manifests" / "project_chunks.jsonl",
        [chunk(path, "ranking backend source code notes", doc_id="project-doc-1")],
    )
    write_jsonl(out / "manifests" / "project_failures.jsonl", [])

    assert cli_main(["semantic-benchmark", "--out", str(out), "--source", "projects", "--query", "backend ranking"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert result["source"] == "projects"
    report_path = Path(result["report_path"])
    assert report_path.exists()
    assert "semantic-fusion" in report_path.read_text(encoding="utf-8")


def test_semantic_benchmark_mcp_returns_report_path(tmp_path: Path) -> None:
    out = tmp_path / "out"
    path = str(tmp_path / "source" / "notes.md")
    write_jsonl(out / "manifests" / "documents.jsonl", [document(path)])
    write_jsonl(out / "manifests" / "chunks.jsonl", [chunk(path, "personal assistant memory retrieval")])
    write_jsonl(out / "manifests" / "failures.jsonl", [])

    result = mcp_semantic_benchmark(
        out_root=str(out),
        source="downloads",
        queries=["assistant memory"],
        limit=4,
    )

    assert result["mcp_version"] == "0.1"
    assert result["status"] == "ok"
    report_path = Path(result["report_path"])
    assert report_path.exists()
    assert "hash-vector-lite" in report_path.read_text(encoding="utf-8")
