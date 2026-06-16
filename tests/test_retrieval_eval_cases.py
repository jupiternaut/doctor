from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main as cli_main
from agent_context.io import read_jsonl, write_jsonl
from agent_context.mcp_server import mcp_retrieval_eval_cases
from agent_context.retrieval_eval import run_retrieval_eval
from agent_context.retrieval_eval_cases import run_retrieval_eval_case_maintenance


def write_download_fixture(out: Path, source_path: str) -> None:
    write_jsonl(
        out / "manifests" / "documents.jsonl",
        [
            {
                "chunk_count": 1,
                "doc_id": "doc-1",
                "extension": ".md",
                "extracted_md_path": source_path,
                "mime": "text/markdown",
                "mtime": "2026-01-01T00:00:00+00:00",
                "parser": "markdown",
                "parser_version": "test",
                "path": source_path,
                "policy": "content",
                "relative_path": Path(source_path).name,
                "scope": "test",
                "sha256": "doc-1",
                "size_bytes": 100,
                "status": "ok",
                "text_chars": 100,
            }
        ],
    )
    write_jsonl(
        out / "manifests" / "chunks.jsonl",
        [
            {
                "char_end": 70,
                "char_start": 0,
                "chunk_id": "doc-1:0001",
                "chunk_index": 1,
                "doc_id": "doc-1",
                "path": source_path,
                "text": "personal assistant memory retrieval ranking recommendation",
                "token_estimate": 20,
            }
        ],
    )
    write_jsonl(out / "manifests" / "failures.jsonl", [])


def test_retrieval_eval_case_maintenance_curates_without_mutating_raw(tmp_path: Path) -> None:
    out = tmp_path / "out"
    raw = out / "feedback" / "retrieval_eval_cases.jsonl"
    raw.parent.mkdir(parents=True)
    records = [
        {
            "query": "assistant memory",
            "source": "downloads",
            "expected_sources": ["/tmp/assistant-memory.md"],
            "origin_id": "arena:a:candidate-1",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "query": "assistant memory duplicate",
            "source": "downloads",
            "expected_sources": ["/tmp/duplicate.md"],
            "origin_id": "arena:a:candidate-1",
        },
        {"query": "missing expected", "source": "downloads"},
        {"query": "unsupported source", "source": "web", "expected_sources": ["/tmp/web.md"]},
        {"goal": "default source case", "expected_path": "/tmp/default.md"},
    ]
    raw_text = "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\nnot-json\n"
    raw.write_text(raw_text, encoding="utf-8")

    result = run_retrieval_eval_case_maintenance(out, default_source="projects")

    assert raw.read_text(encoding="utf-8") == raw_text
    assert result["status"] == "ok"
    assert result["curated_cases"] == 2
    curated = read_jsonl(Path(result["output_cases_path"]))
    assert [case["source"] for case in curated] == ["downloads", "projects"]
    assert curated[0]["case_key"] == "origin:arena:a:candidate-1"
    assert curated[0]["query_family"]
    report = json.loads(Path(result["report_json_path"]).read_text(encoding="utf-8"))
    assert report["summary"]["dropped_malformed"] == 1
    assert report["summary"]["dropped_duplicate"] == 1
    assert report["summary"]["dropped_empty_expected_sources"] == 1
    assert report["summary"]["dropped_unsupported_source"] == 1


def test_retrieval_eval_case_maintenance_cli_and_mcp(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    write_jsonl(
        out / "feedback" / "retrieval_eval_cases.jsonl",
        [{"query": "assistant memory", "source": "downloads", "expected_sources": ["/tmp/assistant.md"]}],
    )

    assert cli_main(["retrieval-eval-cases", "--out", str(out), "--source", "downloads"]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["curated_cases"] == 1
    assert Path(cli_result["output_cases_path"]).exists()

    mcp_result = mcp_retrieval_eval_cases(out_root=str(out), source="downloads")
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["curated_cases"] == 1


def test_retrieval_eval_case_maintenance_can_bootstrap_runtime_cases(tmp_path: Path) -> None:
    out = tmp_path / "out"
    raw = out / "feedback" / "retrieval_eval_cases.jsonl"
    write_jsonl(
        out / "manifests" / "project_documents.jsonl",
        [
            {
                "path": "/repo/src/agent_context/resolver.py",
                "relative_path": "src/agent_context/resolver.py",
            },
            {
                "path": "/repo/src/agent_context/project_index.py",
                "relative_path": "src/agent_context/project_index.py",
            },
        ],
    )

    result = run_retrieval_eval_case_maintenance(out, include_runtime_bootstrap=True)

    assert raw.exists() is False
    assert result["curated_cases"] == 2
    assert result["runtime_bootstrap_included"] == 2
    curated = read_jsonl(Path(result["output_cases_path"]))
    assert {case["origin"] for case in curated} == {"runtime_bootstrap"}
    assert {case["source"] for case in curated} == {"projects"}
    assert {case["expected_sources"][0] for case in curated} == {
        "src/agent_context/resolver.py",
        "src/agent_context/project_index.py",
    }
    report = json.loads(Path(result["report_json_path"]).read_text(encoding="utf-8"))
    assert report["runtime_bootstrap"]["candidate_cases"] >= 2
    assert report["runtime_bootstrap"]["included_cases"] == 2
    assert report["summary"]["runtime_bootstrap_skipped"] >= 1

    mcp_result = mcp_retrieval_eval_cases(out_root=str(out), bootstrap_runtime=True)
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["curated_cases"] == 2


def test_retrieval_eval_defaults_to_curated_cases(tmp_path: Path) -> None:
    out = tmp_path / "out"
    expected_path = str(tmp_path / "source" / "recommendation-memory.md")
    write_download_fixture(out, expected_path)
    write_jsonl(
        out / "feedback" / "retrieval_eval_cases.jsonl",
        [{"query": "assistant memory recommendation", "source": "downloads", "expected_sources": ["/tmp/not-the-source.md"]}],
    )
    write_jsonl(
        out / "feedback" / "retrieval_eval_cases.curated.jsonl",
        [{"query": "assistant memory recommendation", "source": "downloads", "expected_sources": [expected_path]}],
    )

    result = run_retrieval_eval(out, source="downloads", limit=4)

    assert result["summary"]["hash-vector-lite"]["top1_hits"] == 1
    report = json.loads(Path(result["report_json_path"]).read_text(encoding="utf-8"))
    assert report["cases_path"].endswith("retrieval_eval_cases.curated.jsonl")


def test_retrieval_eval_ignores_empty_curated_cases(tmp_path: Path) -> None:
    out = tmp_path / "out"
    expected_path = str(tmp_path / "source" / "recommendation-memory.md")
    write_download_fixture(out, expected_path)
    write_jsonl(
        out / "feedback" / "retrieval_eval_cases.jsonl",
        [{"query": "assistant memory recommendation", "source": "downloads", "expected_sources": [expected_path]}],
    )
    curated = out / "feedback" / "retrieval_eval_cases.curated.jsonl"
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text("", encoding="utf-8")

    result = run_retrieval_eval(out, source="downloads", limit=4)

    assert result["summary"]["hash-vector-lite"]["top1_hits"] == 1
    report = json.loads(Path(result["report_json_path"]).read_text(encoding="utf-8"))
    assert report["cases_path"].endswith("retrieval_eval_cases.jsonl")
