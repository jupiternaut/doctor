from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main as cli_main
from agent_context.io import write_jsonl
from agent_context.mcp_server import mcp_retrieval_eval
from agent_context.retrieval_eval import run_retrieval_eval


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


def write_download_fixture(out: Path, tmp_path: Path) -> str:
    expected_path = str(tmp_path / "source" / "recommendation-memory.md")
    write_jsonl(out / "manifests" / "documents.jsonl", [document(expected_path)])
    write_jsonl(
        out / "manifests" / "chunks.jsonl",
        [chunk(expected_path, "personal assistant memory retrieval ranking recommendation")],
    )
    write_jsonl(out / "manifests" / "failures.jsonl", [])
    return expected_path


def test_retrieval_eval_writes_ranked_report(tmp_path: Path) -> None:
    out = tmp_path / "out"
    expected_path = write_download_fixture(out, tmp_path)
    cases = tmp_path / "cases.jsonl"
    write_jsonl(
        cases,
        [
            {
                "query": "assistant memory recommendation",
                "source": "downloads",
                "expected_sources": [expected_path],
            }
        ],
    )

    result = run_retrieval_eval(out, cases_path=cases, source="downloads", limit=4)

    assert result["status"] == "ok"
    assert result["summary"]["hash-vector-lite"]["top1_hits"] == 1
    assert Path(result["route_selector_model_path"]).exists()
    report = Path(result["report_md_path"]).read_text(encoding="utf-8")
    assert "Retrieval Eval Report" in report
    assert "hash-vector-lite" in report


def test_retrieval_eval_cli_supports_inline_case(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    expected_path = write_download_fixture(out, tmp_path)

    assert (
        cli_main(
            [
                "retrieval-eval",
                "--out",
                str(out),
                "--source",
                "downloads",
                "--case",
                f"assistant memory recommendation => {expected_path}",
            ]
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["hash-vector-lite"]["recall_at_k"] == 1
    assert Path(result["report_json_path"]).exists()


def test_retrieval_eval_mcp_returns_report_paths(tmp_path: Path) -> None:
    out = tmp_path / "out"
    expected_path = write_download_fixture(out, tmp_path)

    result = mcp_retrieval_eval(
        out_root=str(out),
        inline_cases=[f"assistant memory recommendation => {expected_path}"],
        source="downloads",
        limit=4,
    )

    assert result["mcp_version"] == "0.1"
    assert result["status"] == "ok"
    assert result["summary"]["hash-vector-lite"]["mrr"] == 1.0
    assert Path(result["report_md_path"]).exists()
