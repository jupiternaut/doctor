from __future__ import annotations

import json
import os
from pathlib import Path

from agent_context.cli import main
from agent_context.codebase_memory import build_codebase_memory_index, search_codebase_memory
from agent_context.io import write_jsonl, write_text
from agent_context.resolver import resolve_context


def test_codebase_memory_index_builds_pseudo_repo_without_binary(tmp_path: Path) -> None:
    out = tmp_path / "out"
    extracted = out / "extracted" / "abc.md"
    write_text(extracted, "# Extracted\n\n推荐系统 feedback loop.")
    write_jsonl(
        out / "manifests" / "documents.jsonl",
        [
            {
                "doc_id": "sha256:abc123",
                "sha256": "abc123",
                "path": "/Users/example/Downloads/reco.pdf",
                "relative_path": "reco.pdf",
                "extracted_md_path": str(extracted),
                "parser": "markitdown",
                "policy": "extract_text",
                "status": "ok",
            }
        ],
    )

    result = build_codebase_memory_index(out, binary="definitely-missing-codebase-memory-mcp")

    assert result["status"] == "pseudo_repo_ready_binary_missing"
    assert Path(result["pseudo_repo_path"]).exists()
    mappings = read_jsonl(out / "manifests" / "codebase_memory_sources.jsonl")
    assert len(mappings) == 1
    pseudo_path = Path(mappings[0]["pseudo_path"])
    assert pseudo_path.exists()
    assert "doctor_source_path" in pseudo_path.read_text(encoding="utf-8")


def test_codebase_memory_search_uses_external_cli_shape(tmp_path: Path) -> None:
    repo = tmp_path / "pseudo"
    source = repo / "documents" / "aa" / "demo.md"
    source.parent.mkdir(parents=True)
    source.write_text("recommendation feedback loop", encoding="utf-8")
    fake = fake_codebase_memory_binary(tmp_path, repo)

    result = search_codebase_memory(tmp_path / "out", "推荐系统 feedback", binary=str(fake), limit=4)

    assert result["status"] == "ok"
    assert result["sources"][0]["provider"] == "codebase_memory"
    assert result["sources"][0]["source_group"] == "codebase_memory"
    assert result["sources"][0]["path"].endswith("documents/aa/demo.md")


def test_resolver_can_use_codebase_memory_provider(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    repo = tmp_path / "pseudo"
    source = repo / "documents" / "aa" / "demo.md"
    source.parent.mkdir(parents=True)
    source.write_text("recommendation feedback loop", encoding="utf-8")
    fake = fake_codebase_memory_binary(tmp_path, repo)
    monkeypatch.setenv("AGENT_CONTEXT_CODEBASE_MEMORY_BIN", str(fake))

    result = resolve_context(
        out,
        "告诉我本地所有项目里如何构建个人推荐系统",
        source_scope="codebaseMemory",
        limit=3,
    )
    sources = read_jsonl(Path(result["sources_jsonl_path"]))

    assert result["selected_sources"] == ["codebase_memory"]
    assert any(source.get("source_group") == "codebase_memory" for source in sources)


def test_codebase_memory_cli_command_writes_pseudo_repo(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    extracted = out / "extracted" / "abc.md"
    write_text(extracted, "# Extracted\n\ncontext resolver")
    write_jsonl(
        out / "manifests" / "documents.jsonl",
        [
            {
                "doc_id": "sha256:def456",
                "sha256": "def456",
                "path": "/Users/example/Downloads/context.pdf",
                "relative_path": "context.pdf",
                "extracted_md_path": str(extracted),
                "parser": "markitdown",
                "policy": "extract_text",
                "status": "ok",
            }
        ],
    )

    assert main(["codebase-memory-index", "--out", str(out), "--binary", "definitely-missing-codebase-memory-mcp"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "pseudo_repo_ready_binary_missing"
    assert Path(result["pseudo_repo_path"]).exists()


def fake_codebase_memory_binary(tmp_path: Path, repo: Path) -> Path:
    fake = tmp_path / "codebase-memory-mcp"
    fake.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, sys",
                f"repo = {json.dumps(str(repo))}",
                "tool = sys.argv[2]",
                "args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}",
                "if tool == 'list_projects':",
                "    print(json.dumps({'projects': [{'name': 'doctor-pseudo', 'root_path': repo, 'nodes': 3, 'edges': 2}]}))",
                "elif tool == 'search_code':",
                "    print(json.dumps({'results': [{'file': 'documents/aa/demo.md', 'start_line': 1, 'end_line': 1, 'label': 'File', 'qualified_name': 'demo', 'context': 'recommendation feedback loop', 'in_degree': 2, 'out_degree': 3, 'match_lines': [1]}], 'raw_matches': []}))",
                "elif tool == 'index_repository':",
                "    print(json.dumps({'project': 'doctor-pseudo', 'status': 'indexed', 'nodes': 3, 'edges': 2}))",
                "else:",
                "    print(json.dumps({'error': 'unknown tool'}))",
                "    sys.exit(1)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(fake, 0o755)
    return fake


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records
